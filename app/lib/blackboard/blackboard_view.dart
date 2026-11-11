import 'dart:ui' as ui;

import 'package:flutter/material.dart';

import 'model/board_document.dart';
import 'model/board_element.dart';
import 'model/board_event.dart';
import 'model/board_geometry.dart';
import 'model/board_transform.dart';
import 'model/chalk_palette.dart';
import 'render/chalk_text.dart';
import 'render/board_render_cache.dart';
import 'runtime/animation_scheduler.dart';
import 'runtime/blackboard_controller.dart';

/// The tutorial board widget (CONTRACTS §10).
///
/// Six layered [CustomPainter]s — background, text, equation, drawing, graph,
/// annotations — all reading from one [BlackboardController]. Coordinates are
/// normalized [0,1] and mapped to pixels via a single [BoardTransform] (content
/// rect with padding). No WebView, no HTML canvas, no screenshots.
class BlackboardView extends StatelessWidget {
  const BlackboardView({
    super.key,
    required this.controller,
    this.interactive = true,
  });

  final BlackboardController controller;
  final bool interactive;

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: controller.repaint,
      builder: (context, _) {
        return LayoutBuilder(
          builder: (context, constraints) {
            final size = Size(
              constraints.maxWidth.isFinite ? constraints.maxWidth : 600,
              constraints.maxHeight.isFinite ? constraints.maxHeight : 400,
            );
            final transform = BoardTransform.fromSize(size);
            final document = controller.document;

            return CustomPaint(
              size: size,
              painter: _BoardPainter(
                document: document,
                transform: transform,
                animations: controller.animations,
                bounds: BoardBoundsRegistry(),
                interactive: interactive,
              ),
            );
          },
        );
      },
    );
  }
}

/// The single painter that draws all six layers in order.
class _BoardPainter extends CustomPainter {
  _BoardPainter({
    required this.document,
    required this.transform,
    required this.animations,
    required this.bounds,
    required this.interactive,
  });

  final BoardDocument document;
  final BoardTransform transform;
  final BoardAnimationScheduler animations;
  final BoardBoundsRegistry bounds;
  final bool interactive;

  final _textEngine = ChalkTextEngine();

  @override
  void paint(ui.Canvas canvas, ui.Size size) {
    final rect = transform.contentRect;

    // 1. background — matte chalkboard (#171817)
    final bgPaint = Paint()..color = ChalkPalette.board;
    canvas.drawRect(rect, bgPaint);
    final framePaint = Paint()
      ..color = ChalkPalette.frame
      ..style = PaintingStyle.stroke
      ..strokeWidth = 2;
    canvas.drawRect(rect.deflate(1), framePaint);

    // Layer order: 2 text, 3 equation, 4 drawing, 5 graph, 6 annotation.
    // Within a level, insertion order = z-order.
    for (final layer in <int>[2, 3, 4, 5, 6]) {
      final elements = document.elementsForLayer(layer);
      for (final element in elements) {
        _paintElement(canvas, element);
      }
    }

    _textEngine.clear();
  }

  void _paintElement(ui.Canvas canvas, BoardElement element) {
    final progress = animations.progress(element.elementId);
    final resolvedPosition = animations.resolvedPosition(
      element.elementId,
      element.position ?? BoardPoint.zero,
    );
    final color = ChalkPalette.resolve(element.color);

    switch (element.kind) {
      case BoardEventType.writeText:
      case BoardEventType.writeHeading:
      case BoardEventType.writeDefinition:
      case BoardEventType.writeBulletList:
      case BoardEventType.updateText:
        _paintTextElement(canvas, element, resolvedPosition, progress, color);
        break;
      case BoardEventType.writeEquation:
      case BoardEventType.updateEquation:
        _paintEquationElement(canvas, element, resolvedPosition, progress, color);
        break;
      case BoardEventType.highlightEquation:
        _paintHighlightEquation(canvas, element, resolvedPosition, progress, color);
        break;
      case BoardEventType.drawLine:
        _paintDrawLine(canvas, element, resolvedPosition, progress, color);
        break;
      case BoardEventType.drawShape:
        _paintDrawShape(canvas, element, resolvedPosition, progress, color);
        break;
      case BoardEventType.drawPath:
        _paintDrawPath(canvas, element, resolvedPosition, progress, color);
        break;
      case BoardEventType.drawArrow:
        _paintDrawArrow(canvas, element, resolvedPosition, progress, color);
        break;
      case BoardEventType.drawDiagram:
        _paintDrawDiagram(canvas, element, resolvedPosition, progress, color);
        break;
      case BoardEventType.createCoordinateSystem:
        _paintCoordinateSystem(canvas, element, resolvedPosition, progress, color);
        break;
      case BoardEventType.plotFunction:
        _paintPlotFunction(canvas, element, resolvedPosition, progress, color);
        break;
      case BoardEventType.plotPoints:
        _paintPlotPoints(canvas, element, resolvedPosition, progress, color);
        break;
      case BoardEventType.addGraphLabel:
        _paintGraphLabel(canvas, element, resolvedPosition, progress, color);
        break;
      case BoardEventType.highlight:
        _paintHighlight(canvas, element, resolvedPosition, progress, color);
        break;
      case BoardEventType.erase:
      case BoardEventType.eraseElement:
      case BoardEventType.clear:
      case BoardEventType.moveElement:
        break;
      case BoardEventType.unknown:
        break;
    }
  }

  // ----------------------------------------------------------------- text --

  void _paintTextElement(
    ui.Canvas canvas,
    BoardElement element,
    BoardPoint position,
    double progress,
    Color color,
  ) {
    final payload = element.payload;
    final text = _textForTextElement(element);
    if (text == null || text.isEmpty) return;

    final px = transform.sx(position.x);
    final py = transform.sy(position.y);
    final width = transform.width;

    final styleSpec = ChalkTextStyleSpec.forName(
      element.style ?? _styleForType(element.type),
      headingLevel: _headingLevel(element),
      definitionTerm: element.type == BoardEventType.writeDefinition.wireName,
    );
    final fontSizePx = transform.fontPixels(styleSpec.heightFraction);
    final maxWidthPx = (payload['max_width'] as num?)?.toDouble() ?? width;

    _textEngine.paintText(
      canvas,
      text,
      origin: Offset(px, py),
      spec: styleSpec,
      color: color,
      fontSizePx: fontSizePx,
      maxWidthPx: maxWidthPx,
      progress: progress,
    );

    bounds.put(
      element.elementId,
      BoardRect.fromPositionSize(position, BoardSize(0.06, 0.06)),
    );
  }

  String? _textForTextElement(BoardElement element) {
    final payload = element.payload;
    if (payload.containsKey('text')) return payload['text'] as String?;
    if (payload.containsKey('term') && payload.containsKey('text')) {
      return '${payload['term']}: ${payload['text']}';
    }
    if (payload.containsKey('items')) {
      final items = payload['items'];
      if (items is List) return items.join('\n');
    }
    return null;
  }

  String _styleForType(String type) {
    switch (type) {
      case 'write_heading':
        return 'heading';
      case 'write_definition':
        return 'definition';
      case 'write_bullet_list':
        return 'bullet';
      default:
        return 'body';
    }
  }

  int _headingLevel(BoardElement element) {
    final level = element.payload['level'];
    return level is int ? level : 1;
  }

  // ------------------------------------------------------------- equation --

  void _paintEquationElement(
    ui.Canvas canvas,
    BoardElement element,
    BoardPoint position,
    double progress,
    Color color,
  ) {
    final latex = element.payload['latex'] as String?;
    if (latex == null || latex.isEmpty) return;
    final px = transform.sx(position.x);
    final py = transform.sy(position.y);
    final paint = Paint()
      ..color = ChalkPalette.withOpacity(color, 0.9)
      ..style = PaintingStyle.stroke
      ..strokeWidth = transform.strokePixels((element.payload['stroke_width'] as num?)?.toDouble());
    final box = Rect.fromLTWH(px, py, transform.width * 0.4,
        transform.unit * 0.09);
    canvas.drawRRect(
      RRect.fromRectAndRadius(box, const Radius.circular(4)),
      paint,
    );
    final textPainter = TextPainter(
      text: TextSpan(
        text: latex,
        style: TextStyle(
          fontSize: transform.fontPixels(0.03),
          color: color,
          fontFamily: 'ChalkScript',
        ),
      ),
      textDirection: TextDirection.ltr,
    )..layout(maxWidth: box.width);
    textPainter.paint(canvas, Offset(px + 8, py + 4));
    bounds.put(element.elementId, BoardRect.fromLTWH(
      position.x, position.y, 0.4, 0.09,
    ));
  }

  // ------------------------------------------------------------ drawings --

  void _paintDrawLine(
    ui.Canvas canvas,
    BoardElement element,
    BoardPoint position,
    double progress,
    Color color,
  ) {
    final payload = element.payload;
    final start = BoardPoint.tryParse(payload['start']) ?? BoardPoint.zero;
    final end = BoardPoint.tryParse(payload['end']) ?? BoardPoint.zero;
    final strokeWidth = transform.strokePixels((payload['stroke_width'] as num?)?.toDouble());
    final dash = (payload['dash'] as List?)
        ?.whereType<num>()
        .map<double>((e) => e.toDouble())
        .toList();

    final path = Path()
      ..moveTo(transform.sx(start.x), transform.sy(start.y))
      ..lineTo(transform.sx(end.x), transform.sy(end.y));
    _strokePath(canvas, path, color, strokeWidth, dash: dash, progress: progress);
    bounds.put(element.elementId, BoardRect.fromPoints(start, end));
  }

  void _paintDrawShape(
    ui.Canvas canvas,
    BoardElement element,
    BoardPoint position,
    double progress,
    Color color,
  ) {
    final payload = element.payload;
    final shape = payload['shape'] as String?;
    final pos = position;
    final size = element.size ?? const BoardSize(0.2, 0.14);
    final rect = transform.rectToPixels(BoardRect.fromPositionSize(pos, size));
    final strokeWidth = transform.strokePixels((payload['stroke_width'] as num?)?.toDouble());
    final fill = ChalkPalette.resolve(payload['fill_color'],
        fallback: color.withValues(alpha: 0.15));

    final path = Path();
    switch (shape) {
      case 'rect':
        path.addRRect(RRect.fromRectAndRadius(rect, const Radius.circular(6)));
        break;
      case 'circle':
      case 'ellipse':
        path.addOval(rect);
        break;
      case 'triangle':
        path.addPolygon(
          <Offset>[
            Offset(rect.center.dx, rect.top),
            Offset(rect.right, rect.bottom),
            Offset(rect.left, rect.bottom),
          ],
          true,
        );
        break;
      case 'rhombus':
        path.addPolygon(
          <Offset>[
            Offset(rect.center.dx, rect.top),
            Offset(rect.right, rect.center.dy),
            Offset(rect.center.dx, rect.bottom),
            Offset(rect.left, rect.center.dy),
          ],
          true,
        );
        break;
      case 'cylinder':
        path.addOval(rect);
        break;
      case 'polygon':
      default:
        final pts = (payload['points'] as List?)
            ?.whereType<Map<dynamic, dynamic>>()
            .map((e) => BoardPoint.tryParse(e))
            .whereType<BoardPoint>()
            .map((p) => transform.toPixels(p))
            .toList();
        if (pts != null && pts.isNotEmpty) {
          path.addPolygon(pts, true);
        } else {
          path.addRect(rect);
        }
        break;
    }

    final fillPaint = Paint()..color = fill;
    canvas.drawPath(path, fillPaint);
    final strokePaint = Paint()
      ..color = color
      ..style = PaintingStyle.stroke
      ..strokeWidth = strokeWidth;
    canvas.drawPath(path, strokePaint);
    bounds.put(element.elementId, BoardRect.fromLTWH(pos.x, pos.y, size.w, size.h));
  }

  void _paintDrawPath(
    ui.Canvas canvas,
    BoardElement element,
    BoardPoint position,
    double progress,
    Color color,
  ) {
    final payload = element.payload;
    final pts = (payload['points'] as List?)
        ?.whereType<Map<dynamic, dynamic>>()
        .map((e) => BoardPoint.tryParse(e))
        .whereType<BoardPoint>()
        .toList() ?? <BoardPoint>[];
    if (pts.length < 2) return;

    final path = Path()
      ..moveTo(transform.sx(pts.first.x), transform.sy(pts.first.y));
    for (var i = 1; i < pts.length; i++) {
      path.lineTo(transform.sx(pts[i].x), transform.sy(pts[i].y));
    }
    if (payload['closed'] == true) path.close();

    final strokeWidth = transform.strokePixels((payload['stroke_width'] as num?)?.toDouble());
    final fill = ChalkPalette.resolve(payload['fill_color'],
        fallback: color.withValues(alpha: 0.12));
    if (payload['closed'] == true) {
      canvas.drawPath(path, Paint()..color = fill);
    }
    final strokePaint = Paint()
      ..color = color
      ..style = PaintingStyle.stroke
      ..strokeWidth = strokeWidth;
    canvas.drawPath(path, strokePaint);

    final xs = pts.map((p) => p.x);
    final ys = pts.map((p) => p.y);
    final minX = xs.reduce((a, b) => a < b ? a : b);
    final maxX = xs.reduce((a, b) => a > b ? a : b);
    final minY = ys.reduce((a, b) => a < b ? a : b);
    final maxY = ys.reduce((a, b) => a > b ? a : b);
    bounds.put(element.elementId, BoardRect(
      minX, minY, (maxX - minX).clamp(0, 1), (maxY - minY).clamp(0, 1),
    ));
  }

  void _paintDrawArrow(
    ui.Canvas canvas,
    BoardElement element,
    BoardPoint position,
    double progress,
    Color color,
  ) {
    final payload = element.payload;
    final start = BoardPoint.tryParse(payload['start']) ?? BoardPoint.zero;
    final end = BoardPoint.tryParse(payload['end']) ?? BoardPoint.zero;
    final strokeWidth = transform.strokePixels((payload['stroke_width'] as num?)?.toDouble());
    final curve = (payload['curve'] as num?)?.toDouble() ?? 0;

    final path = Path()
      ..moveTo(transform.sx(start.x), transform.sy(start.y));
    if (curve.abs() > 0.01) {
      final control = BoardPoint(
        start.x + (end.x - start.x) * 0.5 + curve * (start.y - end.y) * 0.3,
        start.y + (end.y - start.y) * 0.5 + curve * (end.x - start.x) * 0.3,
      );
      path.quadraticBezierTo(
        transform.sx(control.x),
        transform.sy(control.y),
        transform.sx(end.x),
        transform.sy(end.y),
      );
    } else {
      path.lineTo(transform.sx(end.x), transform.sy(end.y));
    }
    _strokePath(canvas, path, color, strokeWidth, progress: progress);

    // arrowhead
    final arrowPaint = Paint()
      ..color = color
      ..style = PaintingStyle.fill;
    final tip = Offset(transform.sx(end.x), transform.sy(end.y));
    final headSize = transform.unit * 0.025;
    canvas.drawPath(
      Path()
        ..moveTo(tip.dx, tip.dy)
        ..lineTo(tip.dx - headSize * 1.2, tip.dy - headSize * 0.6)
        ..lineTo(tip.dx - headSize * 1.2, tip.dy + headSize * 0.6)
        ..close(),
      arrowPaint,
    );
    final label = payload['label'] as String?;
    if (label != null && label.isNotEmpty) {
      final tp = TextPainter(
        text: TextSpan(
          text: label,
          style: TextStyle(
            fontSize: transform.fontPixels(0.024),
            color: color,
            fontFamily: 'Chalkboard',
          ),
        ),
        textDirection: TextDirection.ltr,
      )..layout(maxWidth: transform.width * 0.3);
      tp.paint(canvas, tip + const Offset(8, -10));
    }
    bounds.put(element.elementId, BoardRect.fromPoints(start, end));
  }

  void _paintDrawDiagram(
    ui.Canvas canvas,
    BoardElement element,
    BoardPoint position,
    double progress,
    Color color,
  ) {
    final payload = element.payload;
    final nodesRaw = payload['nodes'] as List? ?? const [];
    final edgesRaw = payload['edges'] as List? ?? const [];

    // edges first (under nodes)
    for (final raw in edgesRaw) {
      if (raw is! Map) continue;
      final from = raw['from'] as String?;
      final to = raw['to'] as String?;
      if (from == null || to == null) continue;
      final fromRect = bounds[from];
      final toRect = bounds[to];
      if (fromRect == null || toRect == null) continue;
      final p1 = transform.toPixels(fromRect.center);
      final p2 = transform.toPixels(toRect.center);
      final strokeWidth = transform.strokePixels(2.0);
      final edgePath = Path()
        ..moveTo(p1.dx, p1.dy)
        ..lineTo(p2.dx, p2.dy);
      final edgePaint = Paint()
        ..color = color
        ..style = PaintingStyle.stroke
        ..strokeWidth = strokeWidth;
      canvas.drawPath(edgePath, edgePaint);
    }

    // nodes then labels
    for (final raw in nodesRaw) {
      if (raw is! Map) continue;
      final id = raw['id'] as String?;
      final nodePos = BoardPoint.tryParse(raw['position']);
      final nodeSize =
          BoardSize.tryParse(raw['size']) ?? const BoardSize(0.09, 0.06);
      if (nodePos == null || id == null) continue;
      final rect = transform.rectToPixels(
        BoardRect.fromPositionSize(nodePos, nodeSize),
      );
      final nodeColor = ChalkPalette.resolve(raw['color'], fallback: color);
      final shape = raw['shape'] as String?;

      final path = Path();
      switch (shape) {
        case 'circle':
        case 'ellipse':
          path.addOval(rect);
          break;
        default:
          path.addRRect(RRect.fromRectAndRadius(rect, const Radius.circular(8)));
          break;
      }
      final fillPaint =
          Paint()..color = ChalkPalette.withOpacity(nodeColor, 0.18);
      canvas.drawPath(path, fillPaint);
      final strokePaint = Paint()
        ..color = nodeColor
        ..style = PaintingStyle.stroke
        ..strokeWidth = transform.strokePixels(1.5);
      canvas.drawPath(path, strokePaint);

      final label = raw['label'] as String?;
      if (label != null && label.isNotEmpty) {
        final tp = TextPainter(
          text: TextSpan(
            text: label,
            style: TextStyle(
              fontSize: transform.fontPixels(0.026),
              color: color,
              fontFamily: 'Chalkboard',
            ),
          ),
          textDirection: TextDirection.ltr,
        )..layout(maxWidth: rect.width - 12);
        tp.paint(canvas, Offset(rect.left + 6, rect.top + (rect.height - tp.height) / 2));
      }
      bounds.put(id, BoardRect.fromLTWH(nodePos.x, nodePos.y, nodeSize.w, nodeSize.h));
    }
  }

  // ----------------------------------------------------------------- graphs --

  void _paintCoordinateSystem(
    ui.Canvas canvas,
    BoardElement element,
    BoardPoint position,
    double progress,
    Color color,
  ) {
    final payload = element.payload;
    final size = element.size ?? const BoardSize(0.5, 0.5);
    final rect = transform.rectToPixels(
      BoardRect.fromPositionSize(position, size),
    );
    final xRange = (payload['x_range'] as List?)
        ?.whereType<num>()
        .map<double>((e) => e.toDouble())
        .toList();
    final yRange = (payload['y_range'] as List?)
        ?.whereType<num>()
        .map<double>((e) => e.toDouble())
        .toList();
    final bool grid = (payload['grid'] as bool?) ?? true;

    final axisPaint = Paint()
      ..color = ChalkPalette.withOpacity(color, 0.35)
      ..style = PaintingStyle.stroke
      ..strokeWidth = transform.strokePixels(1.2);
    final gridPaint = Paint()
      ..color = ChalkPalette.withOpacity(color, 0.12)
      ..style = PaintingStyle.stroke
      ..strokeWidth = transform.strokePixels(0.6);

    canvas.drawLine(Offset(rect.left, rect.center.dy),
        Offset(rect.right, rect.center.dy), axisPaint);
    canvas.drawLine(Offset(rect.center.dx, rect.top),
        Offset(rect.center.dx, rect.bottom), axisPaint);

    if (grid && xRange != null && yRange != null) {
      final xStep = (payload['x_step'] as num?)?.toDouble() ??
          (xRange[1] - xRange[0]) / 5;
      final yStep = (payload['y_step'] as num?)?.toDouble() ??
          (yRange[1] - yRange[0]) / 5;
      final xMin = xRange[0], xMax = xRange[1];
      final yMin = yRange[0], yMax = yRange[1];
      var x = xMin;
      while (x <= xMax) {
        final px = rect.left + (x - xMin) / (xMax - xMin) * rect.width;
        canvas.drawLine(Offset(px, rect.top), Offset(px, rect.bottom), gridPaint);
        x += xStep;
      }
      var y = yMin;
      while (y <= yMax) {
        final py = rect.bottom - (y - yMin) / (yMax - yMin) * rect.height;
        canvas.drawLine(Offset(rect.left, py), Offset(rect.right, py), gridPaint);
        y += yStep;
      }
    }

    final xLabel = payload['x_label'] as String?;
    final yLabel = payload['y_label'] as String?;
    if (xLabel != null) {
      final tp = TextPainter(
        text: TextSpan(
          text: xLabel,
          style: TextStyle(fontSize: transform.fontPixels(0.024), color: color,
              fontFamily: 'Chalkboard'),
        ),
        textDirection: TextDirection.ltr,
      )..layout();
      tp.paint(canvas, Offset(rect.center.dx - tp.width / 2, rect.bottom + 6));
    }
    if (yLabel != null) {
      final tp = TextPainter(
        text: TextSpan(
          text: yLabel,
          style: TextStyle(fontSize: transform.fontPixels(0.024), color: color,
              fontFamily: 'Chalkboard'),
        ),
        textDirection: TextDirection.ltr,
      )..layout();
      tp.paint(canvas, Offset(rect.left - tp.width - 6, rect.center.dy - tp.height / 2));
    }

    bounds.put(element.elementId, BoardRect.fromLTWH(position.x, position.y, size.w, size.h));
  }

  void _paintPlotFunction(
    ui.Canvas canvas,
    BoardElement element,
    BoardPoint position,
    double progress,
    Color color,
  ) {
    final payload = element.payload;
    final samples = (payload['samples'] as List?)
        ?.whereType<Map<dynamic, dynamic>>()
        .map((e) => BoardPoint.tryParse(e))
        .whereType<BoardPoint>()
        .toList() ?? <BoardPoint>[];
    if (samples.length < 2) return;

    final strokeWidth = transform.strokePixels((payload['stroke_width'] as num?)?.toDouble());
    final style = payload['style'] as String?;

    final path = Path()
      ..moveTo(transform.sx(samples.first.x), transform.sy(samples.first.y));
    for (var i = 1; i < samples.length; i++) {
      path.lineTo(transform.sx(samples[i].x), transform.sy(samples[i].y));
    }
    final dash = style == 'dashed'
        ? <double>[transform.unit * 0.04, transform.unit * 0.03]
        : null;
    _strokePath(canvas, path, color, strokeWidth, dash: dash, progress: progress);

    final xs = samples.map((p) => p.x);
    final ys = samples.map((p) => p.y);
    final minX = xs.reduce((a, b) => a < b ? a : b);
    final maxX = xs.reduce((a, b) => a > b ? a : b);
    final minY = ys.reduce((a, b) => a < b ? a : b);
    final maxY = ys.reduce((a, b) => a > b ? a : b);
    bounds.put(element.elementId, BoardRect(
      minX, minY, (maxX - minX).clamp(0, 1), (maxY - minY).clamp(0, 1),
    ));
  }

  void _paintPlotPoints(
    ui.Canvas canvas,
    BoardElement element,
    BoardPoint position,
    double progress,
    Color color,
  ) {
    final payload = element.payload;
    final pts = (payload['points'] as List?)
        ?.whereType<Map<dynamic, dynamic>>()
        .map((e) => BoardPoint.tryParse(e))
        .whereType<BoardPoint>()
        .toList() ?? <BoardPoint>[];
    for (final point in pts) {
      final pointColor = ChalkPalette.resolve(
        element.payload['color'],
        fallback: color,
      );
      final center = transform.toPixels(point);
      canvas.drawCircle(center, transform.unit * 0.015,
          Paint()..color = pointColor);
    }
    final xs = pts.map((p) => p.x);
    final ys = pts.map((p) => p.y);
    final minX = xs.reduce((a, b) => a < b ? a : b);
    final maxX = xs.reduce((a, b) => a > b ? a : b);
    final minY = ys.reduce((a, b) => a < b ? a : b);
    final maxY = ys.reduce((a, b) => a > b ? a : b);
    bounds.put(element.elementId, BoardRect(
      minX, minY, (maxX - minX).clamp(0, 1), (maxY - minY).clamp(0, 1),
    ));
  }

  void _paintGraphLabel(
    ui.Canvas canvas,
    BoardElement element,
    BoardPoint position,
    double progress,
    Color color,
  ) {
    final payload = element.payload;
    final text = payload['text'] as String?;
    if (text == null || text.isEmpty) return;
    final at = BoardPoint.tryParse(payload['at']) ?? position;
    final center = transform.toPixels(at);
    final tp = TextPainter(
      text: TextSpan(
        text: text,
        style: TextStyle(fontSize: transform.fontPixels(0.026), color: color,
            fontFamily: 'Chalkboard'),
      ),
      textDirection: TextDirection.ltr,
    )..layout();
    tp.paint(canvas, Offset(center.dx + 6, center.dy - tp.height / 2));
    bounds.put(element.elementId, BoardRect.fromLTWH(at.x, at.y,
        tp.width / transform.width, tp.height / transform.height));
  }

  // ----------------------------------------------------------- annotations --

  void _paintHighlight(
    ui.Canvas canvas,
    BoardElement element,
    BoardPoint position,
    double progress,
    Color color,
  ) {
    final payload = element.payload;
    final style = payload['style'] as String? ?? 'box';
    final targetIds = (payload['target_ids'] as List?)
        ?.whereType<String>()
        .toList() ?? <String>[];
    if (targetIds.isEmpty) return;

    final rects = <BoardRect>[];
    for (final id in targetIds) {
      final r = bounds[id];
      if (r != null && !r.isEmpty) rects.add(r);
    }
    if (rects.isEmpty) return;

    final union = rects.reduce((a, b) => a.union(b));
    final pixelRect = transform.rectToPixels(union);
    final highlightColor = ChalkPalette.resolve(payload['color'], fallback: color);

    switch (style) {
      case 'underline':
        final linePaint = Paint()
          ..color = highlightColor
          ..style = PaintingStyle.stroke
          ..strokeWidth = transform.strokePixels(2.5)
          ..strokeCap = StrokeCap.round;
        canvas.drawPath(
          ChalkGeometry.wavyLine(
            Offset(pixelRect.left, pixelRect.bottom + 4),
            Offset(pixelRect.right, pixelRect.bottom + 4),
          ),
          linePaint,
        );
        break;
      case 'circle':
        final circlePaint = Paint()
          ..color = highlightColor
          ..style = PaintingStyle.stroke
          ..strokeWidth = transform.strokePixels(2.0);
        canvas.drawPath(
          ChalkGeometry.wobblyEllipse(pixelRect.inflate(8)),
          circlePaint,
        );
        break;
      case 'box':
      default:
        final boxPaint = Paint()
          ..color = ChalkPalette.withOpacity(highlightColor, 0.25);
        canvas.drawRRect(
          RRect.fromRectAndRadius(pixelRect.inflate(6),
              const Radius.circular(6)),
          boxPaint,
        );
        final strokePaint = Paint()
          ..color = highlightColor
          ..style = PaintingStyle.stroke
          ..strokeWidth = transform.strokePixels(2.0);
        canvas.drawRRect(
          RRect.fromRectAndRadius(pixelRect.inflate(6),
              const Radius.circular(6)),
          strokePaint,
        );
        break;
    }
    final label = payload['label'] as String?;
    if (label != null && label.isNotEmpty) {
      final tp = TextPainter(
        text: TextSpan(
          text: label,
          style: TextStyle(
            fontSize: transform.fontPixels(0.024),
            color: color,
            fontFamily: 'Chalkboard',
          ),
        ),
        textDirection: TextDirection.ltr,
      )..layout();
      tp.paint(canvas, Offset(pixelRect.left, pixelRect.top - tp.height - 6));
    }
    bounds.put(element.elementId, union);
  }

  void _paintHighlightEquation(
    ui.Canvas canvas,
    BoardElement element,
    BoardPoint position,
    double progress,
    Color color,
  ) {
    final payload = element.payload;
    final rect = transform.rectToPixels(BoardRect.fromPositionSize(
      position,
      element.size ?? const BoardSize(0.3, 0.09),
    ));
    final highlightColor = ChalkPalette.resolve(payload['color'], fallback: color);

    final boxPaint = Paint()
      ..color = ChalkPalette.withOpacity(highlightColor, 0.25);
    canvas.drawRRect(
      RRect.fromRectAndRadius(rect.inflate(4), const Radius.circular(4)),
      boxPaint,
    );
    final strokePaint = Paint()
      ..color = highlightColor
      ..style = PaintingStyle.stroke
      ..strokeWidth = transform.strokePixels(2.0);
    canvas.drawRRect(
      RRect.fromRectAndRadius(rect.inflate(4), const Radius.circular(4)),
      strokePaint,
    );
    bounds.put(element.elementId, BoardRect.fromLTWH(position.x, position.y,
        element.size?.w ?? 0.3, element.size?.h ?? 0.09));
  }

  void _strokePath(
    ui.Canvas canvas,
    Path path,
    Color color,
    double strokeWidth, {
    List<double>? dash,
    double progress = 1,
  }) {
    final effectivePath = progress < 1
        ? ChalkGeometry.revealPath(path, progress)
        : path;
    final drawPath = dash == null || dash.isEmpty
        ? effectivePath
        : ChalkGeometry.dashedPath(effectivePath, dash);
    final paint = Paint()
      ..color = color
      ..style = PaintingStyle.stroke
      ..strokeWidth = strokeWidth
      ..strokeCap = StrokeCap.round
      ..strokeJoin = StrokeJoin.round;
    canvas.drawPath(drawPath, paint);
  }

  @override
  bool shouldRepaint(covariant _BoardPainter old) =>
      old.document.lastSequence != document.lastSequence ||
      old.animations.frameEpoch != animations.frameEpoch ||
      old.transform != transform;
}
