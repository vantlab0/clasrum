import 'dart:math' as math;
import 'dart:ui' as ui;

import 'package:flutter/painting.dart';

import '../model/board_geometry.dart';
import '../model/board_transform.dart';
import '../model/chalk_palette.dart';
import '../model/drawing_plan.dart';
import 'board_render_cache.dart' show BoardGeometryCache;
import 'chalk_text.dart';

/// Result of rendering a generative drawing plan.
class GenerativeRenderResult {
  const GenerativeRenderResult({required this.elementBounds});

  /// Normalized bounds of rendered plan elements, keyed by plan element id.
  final Map<String, BoardRect> elementBounds;
}

/// Transform-independent plan metadata reused across progressive frames.
class _PlanGeometry {
  _PlanGeometry({
    required this.timelineById,
    required this.stageWindows,
    required this.order,
  });

  final Map<String, DrawingElementTimeline> timelineById;
  final Map<int, _StageWindow> stageWindows;
  final List<DrawingElement> order;

  static String keyFor(DrawingPlan plan, bool cueGated) {
    final signature = plan.elements
        .map(
          (element) =>
              '${element.id}:${element.stage}:${element.durationMs}:'
              '${element.delayMs}:${element.layer}',
        )
        .join(',');
    return '${plan.elements.length}|${plan.totalDurationMs}|${cueGated ? 1 : 0}|$signature';
  }

  static _PlanGeometry build(DrawingPlan plan, bool cueGated) {
    final timelineById = <String, DrawingElementTimeline>{
      for (final timeline in computeElementTimelines(plan))
        timeline.elementId: timeline,
    };
    final stageWindows = <int, _StageWindow>{};
    if (cueGated) {
      final stageNumbers = <int>{
        for (final element in plan.elements) element.stage,
      };
      for (final stageNumber in stageNumbers) {
        final stageTimelines = plan.elements
            .where((element) => element.stage == stageNumber)
            .map((element) => timelineById[element.id])
            .whereType<DrawingElementTimeline>()
            .toList(growable: false);
        if (stageTimelines.isEmpty) continue;
        var start = stageTimelines.first.startMs;
        var end = stageTimelines.first.endMs;
        for (final timeline in stageTimelines.skip(1)) {
          if (timeline.startMs < start) start = timeline.startMs;
          if (timeline.endMs > end) end = timeline.endMs;
        }
        stageWindows[stageNumber] = _StageWindow(start, end);
      }
    }
    final declarationIndex = <String, int>{
      for (var i = 0; i < plan.elements.length; i++) plan.elements[i].id: i,
    };
    final order = plan.elements.toList(growable: false)
      ..sort((a, b) {
        final layerCmp = a.layer.compareTo(b.layer);
        if (layerCmp != 0) return layerCmp;
        return (declarationIndex[a.id] ?? 0).compareTo(declarationIndex[b.id] ?? 0);
      });
    return _PlanGeometry(
      timelineById: timelineById,
      stageWindows: stageWindows,
      order: order,
    );
  }
}

class _StageWindow {
  const _StageWindow(this.startMs, this.endMs);

  final int startMs;
  final int endMs;
}

/// Renders a [DrawingPlan] progressively onto a [ui.Canvas].
///
/// Coordinates in the plan are normalized board coordinates (0..1). The
/// renderer maps them to pixels through [BoardTransform] and reveals strokes
/// progressively according to the plan timeline (stages, delays, durations,
/// pauses) and the current overall [progress].
class GenerativeDrawingRenderer {
  GenerativeDrawingRenderer._();

  static final ChalkTextEngine _textEngine = ChalkTextEngine();

  static GenerativeRenderResult render(
    ui.Canvas canvas,
    DrawingPlan plan,
    BoardTransform transform, {
    required double progress,
    /// Per-stage reveal progress for cue-gated plans. When supplied, a stage
    /// stays hidden until the matching transcript keyword fires.
    Map<int, double>? stageProgress,
    ChalkTextEngine? textEngine,
    BoardGeometryCache? geometryCache,
    /// Plan-local coordinates are mapped into this board region. When omitted,
    /// the plan keeps the legacy behavior of filling the whole board.
    BoardRect? placement,
  }) {
    final clampedProgress = progress.clamp(0.0, 1.0);
    final engine = textEngine ?? _textEngine;
    final localTransform =
        placement == null ? transform : BoardTransform.forRegion(transform, placement);
    final totalMs = plan.totalDurationMs;
    final elapsedMs = (totalMs * clampedProgress).round();
    final cueGated = stageProgress != null && stageProgress.isNotEmpty;
    final cacheKey = _PlanGeometry.keyFor(plan, cueGated);
    var geometry = geometryCache?.get<_PlanGeometry>(cacheKey);
    geometry ??= _PlanGeometry.build(plan, cueGated);
    geometryCache?.put(cacheKey, geometry);
    final timelineById = geometry.timelineById;
    final stageWindows = geometry.stageWindows;
    final order = geometry.order;

    // Cull plan-local elements outside the visible window before building any
    // paths or text layouts. Panning a long board must not cost O(all elements)
    // every frame.
    final boardVisible = transform.visibleLogicalRect;
    final localVisible = placement == null
        ? boardVisible
        : BoardRect(
            (boardVisible.x - placement.x) / math.max(placement.w, 0.0001),
            (boardVisible.y - placement.y) / math.max(placement.h, 0.0001),
            boardVisible.w / math.max(placement.w, 0.0001),
            boardVisible.h / math.max(placement.h, 0.0001),
          ).clamp01();

    // Resolve timing and visibility once. Clip paths depend on the live
    // transform (including pan/zoom), so only paths needed by the visible
    // elements are rebuilt each frame.
    final revealedElements =
        <({DrawingElement element, double progress, BoardRect bounds})>[];
    final visibleElements =
        <({DrawingElement element, double progress, BoardRect bounds})>[];
    final neededClipIds = <String>{};
    for (final element in order) {
      final timeline = timelineById[element.id];
      double elementProgress;
      if (cueGated) {
        final stage = element.stage;
        final revealed = (stageProgress[stage] ?? (stage <= 1 ? 1.0 : 0.0))
            .clamp(0.0, 1.0);
        if (revealed <= 0.0) continue;
        final window = stageWindows[stage];
        if (timeline == null || window == null || window.endMs <= window.startMs) {
          elementProgress = revealed;
        } else {
          final localStart = timeline.startMs - window.startMs;
          final localEnd = timeline.endMs - window.startMs;
          final localElapsed =
              (revealed * (window.endMs - window.startMs)).round();
          elementProgress = _progressBetween(localElapsed, localStart, localEnd);
        }
      } else {
        elementProgress = _elementProgress(timeline, elapsedMs);
      }
      if (elementProgress <= 0.0) continue;

      final localBounds = element.bounds();
      final entry = (
        element: element,
        progress: elementProgress,
        bounds: localBounds,
      );
      // Published bounds must cover every revealed stage, not only the part
      // currently culled into the viewport; otherwise scrolling changes the
      // element's extent and the layout oscillates.
      revealedElements.add(entry);
      if (!localVisible.intersects(localBounds.inflate(0.02))) continue;

      visibleElements.add(entry);
      final clipId = element.clipId;
      if (clipId != null) neededClipIds.add(clipId);
    }

    final clipPaths = <String, ui.Path>{};
    for (final element in order) {
      if (neededClipIds.contains(element.id) && element.isClosed) {
        final path = _buildClosedPath(element, localTransform);
        if (path != null) clipPaths[element.id] = path;
      }
    }

    final bounds = <String, BoardRect>{};
    for (final entry in revealedElements) {
      bounds[entry.element.id] = placement == null
          ? entry.bounds
          : BoardRect(
              placement.x + entry.bounds.x * placement.w,
              placement.y + entry.bounds.y * placement.h,
              entry.bounds.w * placement.w,
              entry.bounds.h * placement.h,
            );
    }

    for (final entry in visibleElements) {
      final element = entry.element;
      final clipId = element.clipId;
      final clipPath = clipId == null ? null : clipPaths[clipId];
      final hasClip = clipPath != null;

      if (hasClip) {
        canvas.save();
        canvas.clipPath(clipPath);
      }

      try {
        _renderElement(
          canvas,
          element,
          localTransform,
          entry.progress,
          engine,
        );
      } finally {
        if (hasClip) {
          canvas.restore();
        }
      }
    }

    return GenerativeRenderResult(elementBounds: bounds);
  }

  static double _elementProgress(DrawingElementTimeline? timeline, int elapsedMs) {
    if (timeline == null) return 1.0;
    return _progressBetween(elapsedMs, timeline.startMs, timeline.endMs);
  }

  static double _progressBetween(int elapsedMs, int startMs, int endMs) {
    if (elapsedMs <= startMs) return 0.0;
    if (elapsedMs >= endMs) return 1.0;
    final span = endMs - startMs;
    if (span <= 0) return 1.0;
    return ((elapsedMs - startMs) / span).clamp(0.0, 1.0);
  }

  // ---------------------------------------------------------------------------
  // Element dispatch
  // ---------------------------------------------------------------------------

  static void _renderElement(
    ui.Canvas canvas,
    DrawingElement element,
    BoardTransform transform,
    double progress,
    ChalkTextEngine textEngine,
  ) {
    switch (element.kind) {
      case DrawingElementKind.line:
        _renderLine(canvas, element, transform, progress);
        break;
      case DrawingElementKind.polyline:
      case DrawingElementKind.polygon:
        _renderPolyline(canvas, element, transform, progress);
        break;
      case DrawingElementKind.rectangle:
        _renderRectangle(canvas, element, transform, progress);
        break;
      case DrawingElementKind.ellipse:
        _renderEllipse(canvas, element, transform, progress);
        break;
      case DrawingElementKind.arc:
        _renderArc(canvas, element, transform, progress);
        break;
      case DrawingElementKind.quadBezier:
        _renderQuadBezier(canvas, element, transform, progress);
        break;
      case DrawingElementKind.cubicBezier:
        _renderCubicBezier(canvas, element, transform, progress);
        break;
      case DrawingElementKind.path:
        _renderPath(canvas, element, transform, progress);
        break;
      case DrawingElementKind.text:
        _renderText(canvas, element, transform, progress, textEngine);
        break;
      case DrawingElementKind.arrow:
        _renderArrow(canvas, element, transform, progress);
        break;
    }
  }

  // ---------------------------------------------------------------------------
  // Style helpers
  // ---------------------------------------------------------------------------

  static double _strokeWidth(DrawingElement element, BoardTransform transform) {
    final w = element.style?.strokeWidth ?? 2.0;
    return transform.strokePixels(w);
  }

  /// Deterministic bright fallback role so unstyled/complex plans still use
  /// complementary colours instead of a single chalk hue.
  static String _fallbackRole(DrawingElement element) {
    if (element.kind == DrawingElementKind.text) return 'yellow';
    var hash = 0;
    for (final code in element.id.codeUnits) {
      hash = (hash * 31 + code) & 0x7fffffff;
    }
    return ChalkPalette.illustrationRoles[hash % ChalkPalette.illustrationRoles.length];
  }

  static Color _strokeColor(DrawingElement element) {
    final style = element.style;
    final color = ChalkPalette.resolveIllustration(
      style?.strokeColor,
      fallbackRole: _fallbackRole(element),
    );
    final opacity = style?.strokeOpacity ?? 1.0;
    return ChalkPalette.withOpacity(color, opacity);
  }

  static Color? _fillColor(DrawingElement element) {
    final style = element.style;
    final fill = style?.fillColor ?? style?.strokeColor;
    if (fill == null) return null;
    final color = ChalkPalette.resolveIllustration(
      fill,
      fallbackRole: _fallbackRole(element),
    );
    // Translucent but visibly colourful; never muddy or nearly invisible.
    final opacity = (style?.fillOpacity ?? 0.22).clamp(0.08, 0.35);
    return ChalkPalette.withOpacity(color, opacity);
  }

  static ui.StrokeCap _strokeCap(DrawingElement element) {
    switch (element.style?.lineCap) {
      case 'butt':
        return ui.StrokeCap.butt;
      case 'square':
        return ui.StrokeCap.square;
      case 'round':
      default:
        return ui.StrokeCap.round;
    }
  }

  static ui.StrokeJoin _strokeJoin(DrawingElement element) {
    switch (element.style?.lineJoin) {
      case 'miter':
        return ui.StrokeJoin.miter;
      case 'bevel':
        return ui.StrokeJoin.bevel;
      case 'round':
      default:
        return ui.StrokeJoin.round;
    }
  }

  static List<double>? _dashPattern(DrawingElement element, BoardTransform transform) {
    final dash = element.style?.dash;
    if (dash == null || dash.isEmpty) return null;
    // Dash values in protocol are in normalized-ish units; scale to pixels by unit.
    return dash.map((v) => (v * transform.unit * 0.02).clamp(2.0, 80.0)).toList(growable: false);
  }

  static Paint _strokePaint(DrawingElement element, BoardTransform transform, {double alphaMul = 1.0}) {
    final color = _strokeColor(element);
    return Paint()
      ..color = ChalkPalette.withOpacity(color, alphaMul)
      ..style = ui.PaintingStyle.stroke
      ..strokeWidth = _strokeWidth(element, transform)
      ..strokeCap = _strokeCap(element)
      ..strokeJoin = _strokeJoin(element);
  }

  static Paint? _fillPaint(DrawingElement element, {double alphaMul = 1.0}) {
    final color = _fillColor(element);
    if (color == null) return null;
    return Paint()
      ..color = ChalkPalette.withOpacity(color, alphaMul)
      ..style = ui.PaintingStyle.fill;
  }

  /// Splits element progress into stroke and fill phases.
  /// Stroke draws 0..0.75, fill fades 0.65..1.0.
  static double _strokeProgress(double progress) {
    if (progress >= 0.75) return 1.0;
    return (progress / 0.75).clamp(0.0, 1.0);
  }

  static double _fillProgress(double progress) {
    if (progress <= 0.65) return 0.0;
    return ((progress - 0.65) / 0.35).clamp(0.0, 1.0);
  }

  static void _drawStrokedPath(
    ui.Canvas canvas,
    ui.Path path,
    DrawingElement element,
    BoardTransform transform,
    double progress, {
    bool closeForFill = false,
  }) {
    final fillPaint = _fillPaint(element);
    if (fillPaint != null && closeForFill) {
      final fp = _fillProgress(progress);
      if (fp > 0) {
        final p = Paint()
          ..color = ChalkPalette.withOpacity(fillPaint.color, fp)
          ..style = ui.PaintingStyle.fill;
        canvas.drawPath(path, p);
      }
    }

    final sp = _strokeProgress(progress);
    if (sp <= 0) return;
    final revealed = sp < 1.0 ? ChalkGeometry.revealPath(path, sp) : path;
    final dash = _dashPattern(element, transform);
    final drawPath = (dash != null && dash.isNotEmpty)
        ? ChalkGeometry.dashedPath(revealed, dash)
        : revealed;
    canvas.drawPath(drawPath, _strokePaint(element, transform));
  }

  // ---------------------------------------------------------------------------
  // Geometry renderers
  // ---------------------------------------------------------------------------

  static void _renderLine(
    ui.Canvas canvas,
    DrawingElement element,
    BoardTransform transform,
    double progress,
  ) {
    final g = element.geometry;
    if (g is! LineGeometry) return;
    final path = ui.Path()
      ..moveTo(transform.sx(g.start.x), transform.sy(g.start.y))
      ..lineTo(transform.sx(g.end.x), transform.sy(g.end.y));
    _drawStrokedPath(canvas, path, element, transform, progress);
  }

  static void _renderPolyline(
    ui.Canvas canvas,
    DrawingElement element,
    BoardTransform transform,
    double progress,
  ) {
    final g = element.geometry;
    if (g is! PolylineGeometry) return;
    if (g.points.length < 2) return;
    final path = _polylinePath(g, transform);
    _drawStrokedPath(canvas, path, element, transform, progress, closeForFill: g.closed);
  }

  static ui.Path _polylinePath(PolylineGeometry g, BoardTransform transform) {
    final pts = g.points;
    final path = ui.Path();
    if (g.smooth && pts.length >= 3) {
      path.moveTo(transform.sx(pts.first.x), transform.sy(pts.first.y));
      for (var i = 1; i < pts.length - 1; i++) {
        final p0 = pts[i - 1];
        final p1 = pts[i];
        final p2 = pts[i + 1];
        final mid1x = (p0.x + p1.x) / 2;
        final mid1y = (p0.y + p1.y) / 2;
        final mid2x = (p1.x + p2.x) / 2;
        final mid2y = (p1.y + p2.y) / 2;
        path.moveTo(transform.sx(mid1x), transform.sy(mid1y));
        // Build a continuous smooth path: use quadratic through p1 to mid2
      }
      // Rebuild properly as a single continuous path.
      path.reset();
      path.moveTo(transform.sx(pts.first.x), transform.sy(pts.first.y));
      for (var i = 1; i < pts.length - 1; i++) {
        final p1 = pts[i];
        final p2 = pts[i + 1];
        final mid2x = (p1.x + p2.x) / 2;
        final mid2y = (p1.y + p2.y) / 2;
        path.quadraticBezierTo(
          transform.sx(p1.x),
          transform.sy(p1.y),
          transform.sx(mid2x),
          transform.sy(mid2y),
        );
      }
      final last = pts.last;
      path.lineTo(transform.sx(last.x), transform.sy(last.y));
      if (g.closed) {
        // Smooth close back to start
        final pN = pts[pts.length - 1];
        final p0 = pts[0];
        final p1 = pts[1 % pts.length];
        final midNx = (pN.x + p0.x) / 2;
        final midNy = (pN.y + p0.y) / 2;
        final mid0x = (p0.x + p1.x) / 2;
        final mid0y = (p0.y + p1.y) / 2;
        // Rebuild closed smooth path
        path.reset();
        path.moveTo(transform.sx(midNx), transform.sy(midNy));
        for (var i = 0; i < pts.length; i++) {
          final curr = pts[i];
          final next = pts[(i + 1) % pts.length];
          final midx = (curr.x + next.x) / 2;
          final midy = (curr.y + next.y) / 2;
          path.quadraticBezierTo(
            transform.sx(curr.x),
            transform.sy(curr.y),
            transform.sx(midx),
            transform.sy(midy),
          );
        }
        path.close();
      }
    } else {
      path.moveTo(transform.sx(pts.first.x), transform.sy(pts.first.y));
      for (var i = 1; i < pts.length; i++) {
        path.lineTo(transform.sx(pts[i].x), transform.sy(pts[i].y));
      }
      if (g.closed) path.close();
    }
    return path;
  }

  static void _renderRectangle(
    ui.Canvas canvas,
    DrawingElement element,
    BoardTransform transform,
    double progress,
  ) {
    final g = element.geometry;
    if (g is! RectangleGeometry) return;
    final rect = Rect.fromLTWH(
      transform.sx(g.position.x),
      transform.sy(g.position.y),
      g.size.w * transform.width * transform.viewportScale,
      g.size.h * transform.height * transform.viewportScale,
    );
    ui.Path path;
    final r = g.cornerRadius ?? 0.0;
    if (r > 0) {
      final radius = Radius.circular(r * transform.unit * transform.viewportScale);
      path = ui.Path()..addRRect(RRect.fromRectAndRadius(rect, radius));
    } else {
      path = ui.Path()..addRect(rect);
    }
    _drawStrokedPath(canvas, path, element, transform, progress, closeForFill: true);
  }

  static void _renderEllipse(
    ui.Canvas canvas,
    DrawingElement element,
    BoardTransform transform,
    double progress,
  ) {
    final g = element.geometry;
    if (g is! EllipseGeometry) return;
    final rect = Rect.fromCenter(
      center: Offset(transform.sx(g.center.x), transform.sy(g.center.y)),
      width: g.rx * 2 * transform.width * transform.viewportScale,
      height: g.ry * 2 * transform.height * transform.viewportScale,
    );
    final path = ui.Path()..addOval(rect);
    _drawStrokedPath(canvas, path, element, transform, progress, closeForFill: true);
  }

  static void _renderArc(
    ui.Canvas canvas,
    DrawingElement element,
    BoardTransform transform,
    double progress,
  ) {
    final g = element.geometry;
    if (g is! ArcGeometry) return;
    final rect = Rect.fromCenter(
      center: Offset(transform.sx(g.center.x), transform.sy(g.center.y)),
      width: g.rx * 2 * transform.width * transform.viewportScale,
      height: g.ry * 2 * transform.height * transform.viewportScale,
    );
    final startRad = g.startAngle * math.pi / 180.0;
    final sweepRad = g.sweepAngle * math.pi / 180.0;
    final path = ui.Path()..addArc(rect, startRad, sweepRad);
    _drawStrokedPath(canvas, path, element, transform, progress);
  }

  static void _renderQuadBezier(
    ui.Canvas canvas,
    DrawingElement element,
    BoardTransform transform,
    double progress,
  ) {
    final g = element.geometry;
    if (g is! QuadBezierGeometry) return;
    final path = ui.Path()
      ..moveTo(transform.sx(g.start.x), transform.sy(g.start.y))
      ..quadraticBezierTo(
        transform.sx(g.control.x),
        transform.sy(g.control.y),
        transform.sx(g.end.x),
        transform.sy(g.end.y),
      );
    _drawStrokedPath(canvas, path, element, transform, progress);
  }

  static void _renderCubicBezier(
    ui.Canvas canvas,
    DrawingElement element,
    BoardTransform transform,
    double progress,
  ) {
    final g = element.geometry;
    if (g is! CubicBezierGeometry) return;
    final path = ui.Path()
      ..moveTo(transform.sx(g.start.x), transform.sy(g.start.y))
      ..cubicTo(
        transform.sx(g.c1.x),
        transform.sy(g.c1.y),
        transform.sx(g.c2.x),
        transform.sy(g.c2.y),
        transform.sx(g.end.x),
        transform.sy(g.end.y),
      );
    _drawStrokedPath(canvas, path, element, transform, progress);
  }

  static void _renderPath(
    ui.Canvas canvas,
    DrawingElement element,
    BoardTransform transform,
    double progress,
  ) {
    final g = element.geometry;
    if (g is! PathGeometry) return;
    final path = _buildPathFromSegments(g.segments, transform);
    _drawStrokedPath(canvas, path, element, transform, progress, closeForFill: g.hasClose);
  }

  static ui.Path _buildPathFromSegments(List<PathSegment> segments, BoardTransform transform) {
    final path = ui.Path();
    var hasCurrent = false;
    for (final seg in segments) {
      switch (seg.op) {
        case 'move':
          final p = seg.point!;
          path.moveTo(transform.sx(p.x), transform.sy(p.y));
          hasCurrent = true;
          break;
        case 'line':
          final p = seg.point!;
          if (!hasCurrent) {
            path.moveTo(transform.sx(p.x), transform.sy(p.y));
            hasCurrent = true;
          } else {
            path.lineTo(transform.sx(p.x), transform.sy(p.y));
          }
          break;
        case 'quad':
          final c = seg.control!;
          final p = seg.point!;
          if (!hasCurrent) {
            path.moveTo(transform.sx(p.x), transform.sy(p.y));
            hasCurrent = true;
          } else {
            path.quadraticBezierTo(
              transform.sx(c.x), transform.sy(c.y),
              transform.sx(p.x), transform.sy(p.y),
            );
          }
          break;
        case 'cubic':
          final c1 = seg.c1!;
          final c2 = seg.c2!;
          final p = seg.point!;
          if (!hasCurrent) {
            path.moveTo(transform.sx(p.x), transform.sy(p.y));
            hasCurrent = true;
          } else {
            path.cubicTo(
              transform.sx(c1.x), transform.sy(c1.y),
              transform.sx(c2.x), transform.sy(c2.y),
              transform.sx(p.x), transform.sy(p.y),
            );
          }
          break;
        case 'arc':
          final center = seg.center!;
          final rect = Rect.fromCenter(
            center: Offset(transform.sx(center.x), transform.sy(center.y)),
            width: (seg.rx ?? 0) * 2 * transform.width * transform.viewportScale,
            height: (seg.ry ?? 0) * 2 * transform.height * transform.viewportScale,
          );
          final startRad = (seg.startAngle ?? 0) * math.pi / 180.0;
          final sweepRad = (seg.sweepAngle ?? 0) * math.pi / 180.0;
          if (!hasCurrent) {
            path.moveTo(
              rect.center.dx + rect.width / 2 * math.cos(startRad),
              rect.center.dy + rect.height / 2 * math.sin(startRad),
            );
            hasCurrent = true;
          }
          path.arcTo(rect, startRad, sweepRad, false);
          break;
        case 'close':
          path.close();
          hasCurrent = false;
          break;
      }
    }
    return path;
  }

  static void _renderText(
    ui.Canvas canvas,
    DrawingElement element,
    BoardTransform transform,
    double progress,
    ChalkTextEngine textEngine,
  ) {
    final g = element.geometry;
    if (g is! TextGeometry) return;
    final color = _strokeColor(element);
    final fontScale = g.fontScale ?? 1.0;
    final fontSizePx = transform.fontPixels(0.028 * fontScale);
    final maxWidth = g.size != null
        ? g.size!.w * transform.width * transform.viewportScale
        : (transform.width * (1.0 - g.position.x)).clamp(40.0, transform.width);
    final align = g.align == 'center'
        ? TextAlign.center
        : g.align == 'right'
            ? TextAlign.right
            : TextAlign.left;
    final spec = ChalkTextStyleSpec.forName('label');
    textEngine.paintText(
      canvas,
      g.text,
      origin: Offset(transform.sx(g.position.x), transform.sy(g.position.y)),
      spec: spec,
      color: color,
      fontSizePx: fontSizePx,
      maxWidthPx: maxWidth,
      progress: progress,
      textAlign: align,
    );
  }

  static void _renderArrow(
    ui.Canvas canvas,
    DrawingElement element,
    BoardTransform transform,
    double progress,
  ) {
    final g = element.geometry;
    if (g is! ArrowGeometry) return;

    final start = Offset(transform.sx(g.start.x), transform.sy(g.start.y));
    final end = Offset(transform.sx(g.end.x), transform.sy(g.end.y));

    ui.Path shaft;
    Offset tangentEnd = end;
    if (g.curve.abs() > 1e-6) {
      final x1 = g.start.x; final y1 = g.start.y;
      final x2 = g.end.x; final y2 = g.end.y;
      final cx = x1 + (x2 - x1) * 0.5 + g.curve * (y1 - y2) * 0.3;
      final cy = y1 + (y2 - y1) * 0.5 + g.curve * (x2 - x1) * 0.3;
      final control = Offset(transform.sx(cx), transform.sy(cy));
      shaft = ui.Path()
        ..moveTo(start.dx, start.dy)
        ..quadraticBezierTo(control.dx, control.dy, end.dx, end.dy);
      // Tangent at end of quadratic bezier: 2*(end-control)
      tangentEnd = end - control;
      if (tangentEnd.distance < 0.001) tangentEnd = end - start;
    } else {
      shaft = ui.Path()
        ..moveTo(start.dx, start.dy)
        ..lineTo(end.dx, end.dy);
      tangentEnd = end - start;
    }

    // Draw shaft progressively
    final sp = _strokeProgress(progress);
    if (sp > 0) {
      final revealed = sp < 1.0 ? ChalkGeometry.revealPath(shaft, sp) : shaft;
      final dash = _dashPattern(element, transform);
      final drawPath = (dash != null && dash.isNotEmpty)
          ? ChalkGeometry.dashedPath(revealed, dash)
          : revealed;
      canvas.drawPath(drawPath, _strokePaint(element, transform));
    }

    // Arrowhead appears in last 20% of stroke progress
    final headProgress = ((sp - 0.8) / 0.2).clamp(0.0, 1.0);
    if (headProgress > 0) {
      final angle = math.atan2(tangentEnd.dy, tangentEnd.dx);
      final headSize = transform.unit * 0.018 * transform.viewportScale;
      final head = ui.Path()
        ..moveTo(end.dx, end.dy)
        ..lineTo(
          end.dx - headSize * 1.4 * math.cos(angle - 0.45),
          end.dy - headSize * 1.4 * math.sin(angle - 0.45),
        )
        ..lineTo(
          end.dx - headSize * 1.4 * math.cos(angle + 0.45),
          end.dy - headSize * 1.4 * math.sin(angle + 0.45),
        )
        ..close();
      final color = _strokeColor(element);
      canvas.drawPath(
        head,
        Paint()
          ..color = ChalkPalette.withOpacity(color, headProgress)
          ..style = ui.PaintingStyle.fill,
      );
    }
  }

  static ui.Path? _buildClosedPath(DrawingElement element, BoardTransform transform) {
    final g = element.geometry;
    switch (element.kind) {
      case DrawingElementKind.rectangle:
        if (g is! RectangleGeometry) return null;
        final rect = Rect.fromLTWH(
          transform.sx(g.position.x),
          transform.sy(g.position.y),
          g.size.w * transform.width * transform.viewportScale,
          g.size.h * transform.height * transform.viewportScale,
        );
        final r = g.cornerRadius ?? 0.0;
        if (r > 0) {
          final radius = Radius.circular(r * transform.unit * transform.viewportScale);
          return ui.Path()..addRRect(RRect.fromRectAndRadius(rect, radius));
        }
        return ui.Path()..addRect(rect);
      case DrawingElementKind.ellipse:
        if (g is! EllipseGeometry) return null;
        final rect = Rect.fromCenter(
          center: Offset(transform.sx(g.center.x), transform.sy(g.center.y)),
          width: g.rx * 2 * transform.width * transform.viewportScale,
          height: g.ry * 2 * transform.height * transform.viewportScale,
        );
        return ui.Path()..addOval(rect);
      case DrawingElementKind.polygon:
        if (g is! PolylineGeometry) return null;
        final closed = PolylineGeometry(points: g.points, closed: true, smooth: g.smooth);
        return _polylinePath(closed, transform);
      case DrawingElementKind.polyline:
        if (g is! PolylineGeometry || !g.closed) return null;
        return _polylinePath(g, transform);
      case DrawingElementKind.path:
        if (g is! PathGeometry || !g.hasClose) return null;
        return _buildPathFromSegments(g.segments, transform);
      default:
        return null;
    }
  }
}
