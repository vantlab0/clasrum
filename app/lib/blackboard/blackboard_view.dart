import 'dart:math' as math;
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter_math_fork/flutter_math.dart';
import 'package:flutter_math_fork/tex.dart';

import 'model/board_document.dart';
import 'model/board_element.dart';
import 'model/board_event.dart';
import 'model/board_geometry.dart';
import 'model/board_metrics.dart';
import 'model/board_transform.dart';
import 'model/chalk_palette.dart';
import 'model/drawing_plan.dart';
import 'render/chalk_text.dart';
import 'render/board_render_cache.dart';
import 'render/generative_renderer.dart';
import 'render/legacy_drawing_renderer.dart';
import 'runtime/animation_scheduler.dart';
import 'runtime/blackboard_controller.dart';
import 'runtime/board_viewport_controller.dart';
import 'runtime/board_vertical_layout.dart';

/// True when a `draw_diagram` payload uses the legacy step list.
///
/// `steps` and `nodes`/`edges` are alternative encodings of the same diagram;
/// dispatching both would paint the same logical element twice.
bool boardDiagramUsesLegacySteps(Map<String, dynamic> payload) =>
    (payload['steps'] as List?)?.isNotEmpty ?? false;

/// Small breathing room around painted chalk text, in logical pixels.
const double _textPaddingPx = 4.0;

/// On narrow screens never let an explicit (often tiny backend column) wrap
/// width squeeze prose into a tall stack. The board is reflowed vertically, so
/// a generous width is safe and keeps every line readable.
const double _narrowMinWrapFraction = 0.90;

bool _isNarrowBoard(Size size) => BoardVerticalLayout.isNarrow(size);

String? _textForTextElement(BoardElement element) {
  final payload = element.payload;
  final text = payload['text'] as String?;
  if (text != null && text.isNotEmpty) return text;
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
  final raw = element.payload['heading_level'] ??
      element.payload['level'] ??
      element.payload['style'];
  if (raw is String) {
    final value = raw.toLowerCase();
    if (value.length == 2 && value[0] == 'h') {
      return (int.tryParse(value[1]) ?? 1).clamp(1, 6);
    }
  }
  return (asDouble(raw)?.round() ?? 1).clamp(1, 6);
}

/// Logical-pixel wrap width shared by painting and measurement so the reflow
/// heights always match the chalk that is actually drawn.
double _textWrapWidthPx({
  required BoardTransform transform,
  required Map<String, dynamic> payload,
  required double originPx,
  required bool narrow,
}) {
  final maxWidthNorm = (payload['max_width'] as num?)?.toDouble();
  final scaledWidth = transform.width * transform.viewportScale;
  var width = maxWidthNorm != null
      ? maxWidthNorm * scaledWidth - 2 * _textPaddingPx
      : transform.sx(1.0) - originPx - 2 * _textPaddingPx;
  if (narrow && width < scaledWidth * _narrowMinWrapFraction) {
    width = scaledWidth * _narrowMinWrapFraction;
  }
  return width.clamp(40.0, scaledWidth);
}

/// Real measured size for reflow. Text is measured with the same chalk engine
/// and the same narrow wrap width the painter will use, so stacked blocks get
/// their true height on the very first frame (no extra paint pass needed).
BoardRect? _boundsForVerticalReflow({
  required BoardElement element,
  required BoardTransform transform,
  required bool narrow,
  required ChalkTextEngine textEngine,
  required BoardBoundsRegistry bounds,
}) {
  final cached = bounds[element.elementId];
  final text = _textForTextElement(element);
  if (!narrow || text == null || text.isEmpty || element.isLatex) {
    return cached ?? BoardBoundsEstimator.estimate(element);
  }
  final rawStyle = (element.style ??
          element.payload['style'] ??
          _styleForType(element.type))
      .toString()
      .toLowerCase();
  final spec = ChalkTextStyleSpec.forName(
    rawStyle,
    headingLevel: _headingLevel(element),
    definitionTerm: element.type == BoardEventType.writeDefinition.wireName,
  );
  final fontScale =
      (element.payload['font_scale'] as num?)?.toDouble() ?? 1.0;
  final fontSizePx = transform.fontPixels(spec.heightFraction) *
      fontScale.clamp(0.5, 2.5);
  final wrapPx = _textWrapWidthPx(
    transform: transform,
    payload: element.payload,
    originPx: transform.sx(BoardVerticalLayout.sideMargin),
    narrow: true,
  );
  final align = element.payload['align']?.toString();
  final textAlign = align == 'center'
      ? TextAlign.center
      : align == 'right'
          ? TextAlign.right
          : TextAlign.left;
  final sizePx = textEngine.measure(
    text: text,
    spec: spec,
    color: ChalkPalette.resolve(element.color),
    fontSizePx: fontSizePx,
    maxWidthPx: wrapPx,
    textAlign: textAlign,
  );
  return transform.normalizedRect(
    Rect.fromLTWH(0, 0, sizePx.width, sizePx.height),
  );
}

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
    this.viewportController,
  });

  final BlackboardController controller;
  final bool interactive;
  final BoardViewportController? viewportController;

  @override
  Widget build(BuildContext context) {
    final viewport = viewportController;
    final repaint = viewport == null
        ? controller.repaint
        : _CombinedListenable(<Listenable>[
            controller.repaint,
            viewport,
          ]);

    return ListenableBuilder(
      listenable: repaint,
      builder: (context, _) {
        return LayoutBuilder(
          builder: (context, constraints) {
            final size = Size(
              constraints.maxWidth.isFinite ? constraints.maxWidth : 600,
              constraints.maxHeight.isFinite ? constraints.maxHeight : 400,
            );
            var transform = BoardTransform.fromSize(size);
            final viewport = viewportController;
            if (viewport != null) {
              transform = transform.withViewport(
                offset: viewport.offset,
                scale: viewport.scale,
              );
            }
            final document = controller.document;
            final narrow = _isNarrowBoard(size);
            final reflow = BoardVerticalLayout.positions(
              document: document,
              narrow: narrow,
              boundsOf: (element) => _boundsForVerticalReflow(
                element: element,
                transform: transform,
                narrow: narrow,
                textEngine: controller.renderCache.textEngine,
                bounds: controller.renderCache.bounds,
              ),
            );

            return Stack(
              clipBehavior: Clip.hardEdge,
              children: <Widget>[
                CustomPaint(
                  size: size,
                  painter: _BoardPainter(
                    document: document,
                    transform: transform,
                    animations: controller.animations,
                    bounds: controller.renderCache.bounds,
                    interactive: interactive,
                    textEngine: controller.renderCache.textEngine,
                    reflow: reflow,
                    narrow: narrow,
                  ),
                ),
                // LaTeX elements are real flutter_math_fork widgets layered on
                // the same normalized transform. The canvas painter still owns
                // every other layer and owns measured bounds for annotations.
                Positioned.fill(
                  child: _LatexOverlay(
                    controller: controller,
                    transform: transform,
                    reflow: reflow,
                  ),
                ),
              ],
            );
          },
        );
      },
    );
  }
}

/// Merges multiple [Listenable]s into one so a single [ListenableBuilder] can
/// repaint when any source notifies.
class _CombinedListenable extends Listenable {
  _CombinedListenable(this.listenables);

  final List<Listenable> listenables;

  @override
  void addListener(void Function() listener) {
    for (final l in listenables) {
      l.addListener(listener);
    }
  }

  @override
  void removeListener(void Function() listener) {
    for (final l in listenables) {
      l.removeListener(listener);
    }
  }
}

/// Renders LaTeX equations with flutter_math_fork on top of the canvas board.
///
/// Parsed syntax trees are cached by source string: animation frames only
/// rebuild clip rectangles, they do not reparse TeX on every repaint.
class _LatexOverlay extends StatefulWidget {
  const _LatexOverlay({
    required this.controller,
    required this.transform,
    required this.reflow,
  });

  final BlackboardController controller;
  final BoardTransform transform;
  final Map<String, BoardPoint> reflow;

  @override
  State<_LatexOverlay> createState() => _LatexOverlayState();
}

class _LatexOverlayState extends State<_LatexOverlay> {
  static const int _maxCachedExpressions = 128;
  final Map<String, SyntaxTree?> _parsed = <String, SyntaxTree?>{};

  @override
  Widget build(BuildContext context) {
    final document = widget.controller.document;
    final equations = document.elementsInPaintOrder.where((e) => e.isLatex);
    return IgnorePointer(
      child: Stack(
        clipBehavior: Clip.hardEdge,
        children: <Widget>[
          for (final element in equations) _equation(element),
        ],
      ),
    );
  }

  Widget _equation(BoardElement element) {
    final expression = (element.latex ?? element.payload['text']?.toString() ?? '')
        .trim();
    if (expression.isEmpty) return const SizedBox.shrink();

    final position = widget.controller.animations.resolvedPosition(
      element.elementId,
      widget.reflow[element.elementId] ??
          element.position ??
          BoardPoint.zero,
    );
    final estimated = BoardBoundsEstimator.estimate(element);
    final width = element.size?.w ?? estimated?.w ?? 0.34;
    final height = element.size?.h ?? estimated?.h ?? 0.09;
    final rect = Rect.fromLTWH(
      widget.transform.sx(position.x),
      widget.transform.sy(position.y),
      math.max(24.0, width * widget.transform.width * widget.transform.viewportScale),
      math.max(
        20.0,
        height * widget.transform.height * widget.transform.viewportScale,
      ),
    );
    final progress = widget.controller.animations
        .progress(element.elementId)
        .clamp(0.0, 1.0);
    final color = ChalkPalette.resolve(element.color);
    final fontScale = (element.payload['font_scale'] as num?)?.toDouble() ?? 1.0;
    final fontSize = widget.transform.fontPixels(0.034, fontScale: fontScale);

    return Positioned.fromRect(
      rect: rect,
      child: ClipRect(
        clipper: _ProgressiveRevealClipper(progress),
        child: SizedBox.expand(
          child: FittedBox(
            fit: BoxFit.scaleDown,
            alignment: Alignment.topLeft,
            child: _mathWidget(expression, fontSize, color),
          ),
        ),
      ),
    );
  }

  Widget _mathWidget(String expression, double fontSize, Color color) {
    final textStyle = TextStyle(
      fontSize: fontSize,
      color: color,
      height: 1.15,
      decoration: TextDecoration.none,
    );
    final ast = _parse(expression);
    if (ast != null) {
      return Math(
        ast: ast,
        mathStyle: MathStyle.text,
        textStyle: textStyle,
        textScaleFactor: 1.0,
      );
    }
    // Invalid TeX still renders a readable fallback rather than crashing the
    // board; Math.tex owns ParseException handling.
    return Math.tex(
      expression,
      mathStyle: MathStyle.text,
      textStyle: textStyle,
      textScaleFactor: 1.0,
      onErrorFallback: (error) => Text(
        error.messageWithType,
        style: TextStyle(fontSize: fontSize, color: color),
      ),
    );
  }

  SyntaxTree? _parse(String expression) {
    if (_parsed.containsKey(expression)) return _parsed[expression];
    SyntaxTree? tree;
    try {
      tree = SyntaxTree(
        greenRoot: TexParser(
          expression,
          const TexParserSettings(),
        ).parse(),
      );
    } catch (_) {
      tree = null;
    }
    if (_parsed.length >= _maxCachedExpressions) _parsed.clear();
    _parsed[expression] = tree;
    return tree;
  }
}

class _ProgressiveRevealClipper extends CustomClipper<Rect> {
  const _ProgressiveRevealClipper(this.progress);

  final double progress;

  @override
  Rect getClip(Size size) =>
      Rect.fromLTWH(0, 0, size.width * progress, size.height);

  @override
  bool shouldReclip(_ProgressiveRevealClipper oldClipper) =>
      oldClipper.progress != progress;
}

/// The single painter that draws all six layers in order.
class _BoardPainter extends CustomPainter {
  _BoardPainter({
    required this.document,
    required this.transform,
    required this.animations,
    required this.bounds,
    required this.interactive,
    required this.textEngine,
    required this.reflow,
    required this.narrow,
  });

  final BoardDocument document;
  final BoardTransform transform;
  final BoardAnimationScheduler animations;
  final BoardBoundsRegistry bounds;
  final bool interactive;
  final ChalkTextEngine textEngine;
  final Map<String, BoardPoint> reflow;
  final bool narrow;

  ChalkTextEngine get _textEngine => textEngine;

  @override
  void paint(ui.Canvas canvas, ui.Size size) {
    try {
      final rect = transform.contentRect;
      // Drop measurement bounds for erased/removed elements so a restored board
      // cannot reuse stale geometry from a previous revision.
      bounds.retainOnly(document.elements.keys.toSet());

      // 1. background — true black board (no border frame)
      final bgPaint = Paint()..color = ChalkPalette.board;
      canvas.drawRect(rect, bgPaint);

      // Layer order: 2 text, 3 equation, 4 drawing, 5 graph, 6 annotation.
      // Within a level, insertion order = z-order.
      for (final layer in <int>[2, 3, 4, 5, 6]) {
        final elements = document.elementsForLayer(layer);
        for (final element in elements) {
          _paintElement(canvas, element);
        }
      }
    } catch (_) {
      // Never crash the board painter; keep the last stable frame.
      // The text measurement cache is intentionally retained across frames.
    }
  }

  void _paintElement(ui.Canvas canvas, BoardElement element) {
    try {
      final progress = animations.progress(element.elementId);
      final resolvedPosition = animations.resolvedPosition(
        element.elementId,
        reflow[element.elementId] ?? element.position ?? BoardPoint.zero,
      );
      final color = ChalkPalette.resolve(element.color);

      switch (element.kind) {
      case BoardEventType.writeText:
      case BoardEventType.writeHeading:
      case BoardEventType.writeDefinition:
      case BoardEventType.writeBulletList:
      case BoardEventType.updateText:
        // Direct board writes with style=equation travel as write_text. Route
        // them to the LaTeX painter instead of drawing raw markup as prose.
        if (element.isLatex) {
          _paintEquationElement(canvas, element, resolvedPosition, progress, color);
        } else {
          _paintTextElement(canvas, element, resolvedPosition, progress, color);
        }
        break;
      case BoardEventType.writeEquation:
      case BoardEventType.updateEquation:
        _paintEquationElement(
            canvas, element, resolvedPosition, progress, color);
        break;
      case BoardEventType.highlightEquation:
        _paintHighlightEquation(
            canvas, element, resolvedPosition, progress, color);
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
        // A diagram is one logical element. Legacy `steps` and modern
        // `nodes`/`edges` are alternative encodings, never two paint passes.
        if (boardDiagramUsesLegacySteps(element.payload)) {
          _paintDrawing(canvas, element, resolvedPosition, progress, color);
        } else {
          _paintDrawDiagram(canvas, element, resolvedPosition, progress, color);
        }
        break;
      case BoardEventType.drawPlan:
        _paintDrawPlan(canvas, element, resolvedPosition, progress, color);
        break;
      case BoardEventType.createCoordinateSystem:
        _paintCoordinateSystem(
            canvas, element, resolvedPosition, progress, color);
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
      case BoardEventType.viewportFocus:
        // Viewport focus does not produce rendered board content.
        break;
      case BoardEventType.unknown:
        break;
    }
    } catch (_) {
      // Never crash the board painter due to a malformed element.
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
    // Equation-style text is routed by the caller to [_paintEquationElement]
    // and rendered by [_LatexOverlay], never as prose on this canvas.
    final rawStyle = (element.style ?? payload['style'] ?? _styleForType(element.type))
        .toString()
        .toLowerCase();

    final text = _textForTextElement(element);
    if (text == null || text.isEmpty) return;

    final px = transform.sx(position.x);
    final py = transform.sy(position.y);

    final headingLevel = _headingLevel(element);
    final styleSpec = ChalkTextStyleSpec.forName(
      rawStyle,
      headingLevel: headingLevel,
      definitionTerm: element.type == BoardEventType.writeDefinition.wireName,
    );

    final fontScale = (payload['font_scale'] as num?)?.toDouble() ?? 1.0;
    final fontSizePx = transform.fontPixels(styleSpec.heightFraction) * fontScale.clamp(0.5, 2.5);
    // TextPainter is the source of truth for wrapping. Its max width uses the
    // viewport scale and, on narrow screens, a generous minimum so prose never
    // collapses into a tall overlapping stack.
    final maxWidthPx = _textWrapWidthPx(
      transform: transform,
      payload: payload,
      originPx: px,
      narrow: narrow,
    );

    final alignStr = (payload['align'] as String?) ?? 'left';
    final textAlign = alignStr == 'center'
        ? TextAlign.center
        : alignStr == 'right'
            ? TextAlign.right
            : TextAlign.left;

    // Highlight behind text (no opaque white).
    final highlight = payload['highlight'] == true;
    if (highlight) {
      final layout = _textEngine.layout(
        text: text,
        spec: styleSpec,
        color: color,
        fontSizePx: fontSizePx,
        maxWidthPx: maxWidthPx,
        textAlign: textAlign,
      );
      final highlightColor = ChalkPalette.resolve(
        payload['highlight_color'] ?? 'yellow',
        fallback: ChalkPalette.yellow,
      );
      final bgRect = Rect.fromLTWH(
        px - 4,
        py - 2,
        layout.width + 8,
        layout.height + 4,
      );
      final bgPaint = Paint()
        ..color = ChalkPalette.withOpacity(highlightColor, 0.22)
        ..style = PaintingStyle.fill;
      canvas.drawRRect(
        RRect.fromRectAndRadius(bgRect, const Radius.circular(4)),
        bgPaint,
      );
    }

    final paintedRect = _textEngine.paintText(
      canvas,
      text,
      origin: Offset(px, py),
      spec: styleSpec,
      color: color,
      fontSizePx: fontSizePx,
      maxWidthPx: maxWidthPx,
      progress: progress,
      textAlign: textAlign,
    );

    // Convert the TextPainter-painted rect through the viewport-aware inverse
    // so measured bounds stay truthful while zoomed or panned. They are what
    // collision checks, focus and viewport clamping should consume.
    final measured = transform.normalizedRect(paintedRect);
    final padX = _textPaddingPx / (transform.width * transform.viewportScale);
    final padY = _textPaddingPx / (transform.height * transform.viewportScale);
    final padded = BoardRect(
      measured.x - padX,
      measured.y - padY,
      measured.w + 2 * padX,
      measured.h + 2 * padY,
    ).clamp01();
    bounds.put(
      element.elementId,
      padded.isEmpty
          ? BoardRect.fromPositionSize(position, BoardSize(0.2, 0.05))
          : padded,
    );
  }

  /// Paints every non-prose label through the same chalk engine as board text.
  ///
  /// Raw TextPainter labels ignored `font_scale`, reveal progress, grain and
  /// the H1-H6 style contract, which made diagrams look doubled/scattered
  /// next to ordinary chalk writing.
  Size _paintChalkLabel(
    ui.Canvas canvas,
    String text,
    Offset origin, {
    required Color color,
    required double progress,
    required double maxWidthPx,
    Map<String, dynamic>? payload,
    String style = 'label',
  }) {
    final spec = ChalkTextStyleSpec.forName(style);
    final fontScale =
        ((payload?['font_scale'] as num?)?.toDouble() ?? 1.0).clamp(0.5, 2.5);
    final painted = _textEngine.paintText(
      canvas,
      text,
      origin: origin,
      spec: spec,
      color: color,
      fontSizePx: transform.fontPixels(spec.heightFraction) * fontScale,
      maxWidthPx: math.max(24.0, maxWidthPx),
      progress: progress,
    );
    return Size(painted.width, painted.height);
  }

  // ------------------------------------------------------------- equation --

  void _paintEquationElement(
    ui.Canvas canvas,
    BoardElement element,
    BoardPoint position,
    double progress,
    Color color,
  ) {
    // LaTeX is painted by [_LatexOverlay] using flutter_math_fork. The canvas
    // only publishes measured bounds so highlights and collision checks wrap
    // the real formula rather than a placeholder box.
    final estimated = BoardBoundsEstimator.estimate(element);
    final width = element.size?.w ?? estimated?.w ?? 0.34;
    final height = element.size?.h ?? estimated?.h ?? 0.09;
    bounds.put(
      element.elementId,
      BoardRect.fromLTWH(position.x, position.y, width, height),
    );
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
    final strokeWidth =
        transform.strokePixels((payload['stroke_width'] as num?)?.toDouble());
    final dash = (payload['dash'] as List?)
        ?.whereType<num>()
        .map<double>((e) => e.toDouble())
        .toList();

    final path = Path()
      ..moveTo(transform.sx(start.x), transform.sy(start.y))
      ..lineTo(transform.sx(end.x), transform.sy(end.y));
    _strokePath(canvas, path, color, strokeWidth,
        dash: dash, progress: progress);
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
    final strokeWidth =
        transform.strokePixels((payload['stroke_width'] as num?)?.toDouble());
    final rawFill = payload['fill_color'];
    final Color? fill = rawFill == null
        ? null
        : ChalkPalette.withOpacity(
            ChalkPalette.resolve(rawFill, fallback: color),
            0.25,
          );

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

    if (fill != null) {
      canvas.drawPath(path, Paint()..color = fill);
    }
    final strokePaint = Paint()
      ..color = color
      ..style = PaintingStyle.stroke
      ..strokeWidth = strokeWidth;
    canvas.drawPath(path, strokePaint);
    bounds.put(
        element.elementId, BoardRect.fromLTWH(pos.x, pos.y, size.w, size.h));
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
            .toList() ??
        <BoardPoint>[];
    if (pts.length < 2) return;

    final path = Path()
      ..moveTo(transform.sx(pts.first.x), transform.sy(pts.first.y));
    for (var i = 1; i < pts.length; i++) {
      path.lineTo(transform.sx(pts[i].x), transform.sy(pts[i].y));
    }
    if (payload['closed'] == true) path.close();

    final strokeWidth =
        transform.strokePixels((payload['stroke_width'] as num?)?.toDouble());
    final rawFill = payload['fill_color'];
    if (payload['closed'] == true && rawFill != null) {
      final fill = ChalkPalette.withOpacity(
        ChalkPalette.resolve(rawFill, fallback: color),
        0.22,
      );
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
    bounds.put(
        element.elementId,
        BoardRect(
          minX,
          minY,
          (maxX - minX).clamp(0, 1),
          (maxY - minY).clamp(0, 1),
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
    final strokeWidth =
        transform.strokePixels((payload['stroke_width'] as num?)?.toDouble());
    final curve = (payload['curve'] as num?)?.toDouble() ?? 0;

    final path = Path()..moveTo(transform.sx(start.x), transform.sy(start.y));
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
      _paintChalkLabel(
        canvas,
        label,
        tip + const Offset(8, -10),
        color: color,
        progress: progress,
        maxWidthPx: transform.width * 0.3,
        payload: element.payload,
      );
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

    // Register same-event node geometry before resolving edges. Previously
    // edges were drawn first and their own endpoints were missing from the
    // bounds registry, so most diagrams silently lost their connections.
    final nodeBounds = <String, BoardRect>{};
    final nodeRects = <String, ui.Rect>{};
    for (final raw in nodesRaw) {
      if (raw is! Map) continue;
      final id = raw['id'] as String?;
      final nodePos = BoardPoint.tryParse(raw['position']);
      final nodeSize =
          BoardSize.tryParse(raw['size']) ?? const BoardSize(0.09, 0.06);
      if (nodePos == null || id == null) continue;
      final logical = BoardRect.fromPositionSize(nodePos, nodeSize);
      nodeBounds[id] = logical;
      nodeRects[id] = transform.rectToPixels(logical);
      bounds.put(id, logical);
    }

    // Edges first (under nodes), revealed with the same progress clock.
    for (final raw in edgesRaw) {
      if (raw is! Map) continue;
      final fromId = raw['from'] as String?;
      final toId = raw['to'] as String?;
      if (fromId == null || toId == null) continue;
      final from = bounds[fromId];
      final to = bounds[toId];
      if (from == null || to == null) continue;
      final p1 = transform.toPixels(from.center);
      final p2 = transform.toPixels(to.center);
      final edgePath = Path()
        ..moveTo(p1.dx, p1.dy)
        ..lineTo(p2.dx, p2.dy);
      _strokePath(
        canvas,
        edgePath,
        color,
        transform.strokePixels(2.0),
        progress: progress,
      );
    }

    // Nodes and chalk labels.
    for (final raw in nodesRaw) {
      if (raw is! Map) continue;
      final id = raw['id'] as String?;
      final rect = id == null ? null : nodeRects[id];
      if (rect == null) continue;
      final nodeColor = ChalkPalette.resolve(raw['color'], fallback: color);
      final visibleColor = ChalkPalette.withOpacity(nodeColor, progress);

      final path = Path();
      switch (raw['shape'] as String?) {
        case 'circle':
        case 'ellipse':
          path.addOval(rect);
          break;
        default:
          path.addRRect(
              RRect.fromRectAndRadius(rect, const Radius.circular(8)));
          break;
      }
      canvas.drawPath(
        path,
        Paint()..color = ChalkPalette.withOpacity(visibleColor, 0.18),
      );
      canvas.drawPath(
        path,
        Paint()
          ..color = visibleColor
          ..style = PaintingStyle.stroke
          ..strokeWidth = transform.strokePixels(1.5),
      );

      final label = raw['label'] as String?;
      if (label != null && label.isNotEmpty) {
        final spec = ChalkTextStyleSpec.forName('label');
        final fontScale = ((element.payload['font_scale'] as num?)?.toDouble() ??
                1.0).clamp(0.5, 2.5);
        final labelSize = _textEngine.measure(
          text: label,
          spec: spec,
          color: nodeColor,
          fontSizePx: transform.fontPixels(spec.heightFraction) * fontScale,
          maxWidthPx: math.max(24.0, rect.width - 12),
        );
        _paintChalkLabel(
          canvas,
          label,
          Offset(
            rect.left + 6,
            rect.top + math.max(0.0, (rect.height - labelSize.height) / 2),
          ),
          color: nodeColor,
          progress: progress,
          maxWidthPx: rect.width - 12,
          payload: element.payload,
        );
      }
    }

    if (nodeBounds.isNotEmpty) {
      var union = nodeBounds.values.first;
      for (final box in nodeBounds.values.skip(1)) {
        union = union.union(box);
      }
      bounds.put(element.elementId, union.clamp01());
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
        canvas.drawLine(
            Offset(px, rect.top), Offset(px, rect.bottom), gridPaint);
        x += xStep;
      }
      var y = yMin;
      while (y <= yMax) {
        final py = rect.bottom - (y - yMin) / (yMax - yMin) * rect.height;
        canvas.drawLine(
            Offset(rect.left, py), Offset(rect.right, py), gridPaint);
        y += yStep;
      }
    }

    final xLabel = payload['x_label'] as String?;
    final yLabel = payload['y_label'] as String?;
    if (xLabel != null && xLabel.isNotEmpty) {
      final size = _textEngine.measure(
        text: xLabel,
        spec: ChalkTextStyleSpec.forName('label'),
        color: color,
        fontSizePx: transform.fontPixels(ChalkTextStyleSpec.forName('label').heightFraction),
        maxWidthPx: rect.width,
      );
      _paintChalkLabel(
        canvas,
        xLabel,
        Offset(rect.center.dx - size.width / 2, rect.bottom + 6),
        color: color,
        progress: progress,
        maxWidthPx: rect.width,
        payload: element.payload,
      );
    }
    if (yLabel != null && yLabel.isNotEmpty) {
      final spec = ChalkTextStyleSpec.forName('label');
      final size = _textEngine.measure(
        text: yLabel,
        spec: spec,
        color: color,
        fontSizePx: transform.fontPixels(spec.heightFraction),
        maxWidthPx: rect.width,
      );
      _paintChalkLabel(
        canvas,
        yLabel,
        Offset(rect.left - size.width - 6, rect.center.dy - size.height / 2),
        color: color,
        progress: progress,
        maxWidthPx: rect.width,
        payload: element.payload,
      );
    }

    bounds.put(element.elementId,
        BoardRect.fromLTWH(position.x, position.y, size.w, size.h));
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
            .toList() ??
        <BoardPoint>[];
    if (samples.length < 2) return;

    final strokeWidth =
        transform.strokePixels((payload['stroke_width'] as num?)?.toDouble());
    final style = payload['style'] as String?;

    final path = Path()
      ..moveTo(transform.sx(samples.first.x), transform.sy(samples.first.y));
    for (var i = 1; i < samples.length; i++) {
      path.lineTo(transform.sx(samples[i].x), transform.sy(samples[i].y));
    }
    final dash = style == 'dashed'
        ? <double>[transform.unit * 0.04, transform.unit * 0.03]
        : null;
    _strokePath(canvas, path, color, strokeWidth,
        dash: dash, progress: progress);

    final xs = samples.map((p) => p.x);
    final ys = samples.map((p) => p.y);
    final minX = xs.reduce((a, b) => a < b ? a : b);
    final maxX = xs.reduce((a, b) => a > b ? a : b);
    final minY = ys.reduce((a, b) => a < b ? a : b);
    final maxY = ys.reduce((a, b) => a > b ? a : b);
    bounds.put(
        element.elementId,
        BoardRect(
          minX,
          minY,
          (maxX - minX).clamp(0, 1),
          (maxY - minY).clamp(0, 1),
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
            .toList() ??
        <BoardPoint>[];
    for (final point in pts) {
      final pointColor = ChalkPalette.resolve(
        element.payload['color'],
        fallback: color,
      );
      final center = transform.toPixels(point);
      canvas.drawCircle(
          center, transform.unit * 0.015, Paint()..color = pointColor);
    }
    final xs = pts.map((p) => p.x);
    final ys = pts.map((p) => p.y);
    final minX = xs.reduce((a, b) => a < b ? a : b);
    final maxX = xs.reduce((a, b) => a > b ? a : b);
    final minY = ys.reduce((a, b) => a < b ? a : b);
    final maxY = ys.reduce((a, b) => a > b ? a : b);
    bounds.put(
        element.elementId,
        BoardRect(
          minX,
          minY,
          (maxX - minX).clamp(0, 1),
          (maxY - minY).clamp(0, 1),
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
    final size = _paintChalkLabel(
      canvas,
      text,
      Offset(center.dx + 6, center.dy),
      color: color,
      progress: progress,
      maxWidthPx: transform.width * 0.3,
      payload: element.payload,
    );
    bounds.put(
        element.elementId,
        BoardRect.fromLTWH(at.x, at.y, size.width / transform.width,
            size.height / transform.height));
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
    final targetIds =
        (payload['target_ids'] as List?)?.whereType<String>().toList() ??
            <String>[];
    if (targetIds.isEmpty) return;

    final rects = <BoardRect>[];
    for (final id in targetIds) {
      final r = bounds[id];
      if (r != null && !r.isEmpty) rects.add(r);
    }
    if (rects.isEmpty) return;

    final union = rects.reduce((a, b) => a.union(b));
    final pixelRect = transform.rectToPixels(union);
    final highlightColor =
        ChalkPalette.resolve(payload['color'], fallback: color);

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
          RRect.fromRectAndRadius(
              pixelRect.inflate(6), const Radius.circular(6)),
          boxPaint,
        );
        final strokePaint = Paint()
          ..color = highlightColor
          ..style = PaintingStyle.stroke
          ..strokeWidth = transform.strokePixels(2.0);
        canvas.drawRRect(
          RRect.fromRectAndRadius(
              pixelRect.inflate(6), const Radius.circular(6)),
          strokePaint,
        );
        break;
    }
    final label = payload['label'] as String?;
    if (label != null && label.isNotEmpty) {
      final spec = ChalkTextStyleSpec.forName('label');
      final labelSize = _textEngine.measure(
        text: label,
        spec: spec,
        color: highlightColor,
        fontSizePx: transform.fontPixels(spec.heightFraction),
        maxWidthPx: pixelRect.width,
      );
      _paintChalkLabel(
        canvas,
        label,
        Offset(pixelRect.left, pixelRect.top - labelSize.height - 6),
        color: highlightColor,
        progress: progress,
        maxWidthPx: pixelRect.width,
        payload: element.payload,
      );
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
    final highlightColor =
        ChalkPalette.resolve(payload['color'], fallback: color);

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
    bounds.put(
        element.elementId,
        BoardRect.fromLTWH(position.x, position.y, element.size?.w ?? 0.3,
            element.size?.h ?? 0.09));
  }

  void _paintDrawing(
    ui.Canvas canvas,
    BoardElement element,
    BoardPoint position,
    double progress,
    Color color,
  ) {
    final payload = parseDrawingPayload(element.payload);

    final steps = payload.steps;
    if (steps.isEmpty) return;

    final sz = element.size ?? BoardSize(0.3, 0.2);

    final result = DrawingRenderer.render(
      canvas,
      steps,
      transform,
      position,
      sz,
      progress: progress,
    );

    // Write measured bounds so highlights can target drawing parts.
    for (final id in result.elementIds) {
      final b = result.bounds[id];
      if (b != null) {
        bounds.put(id, b);
      }
    }
  }

  static final Set<String> _loggedPlanRenders = <String>{};

  void _paintDrawPlan(
    ui.Canvas canvas,
    BoardElement element,
    BoardPoint position,
    double progress,
    Color color,
  ) {
    final plan = DrawingPlan.tryParse(element.payload);
    if (plan == null) return;
    if (_loggedPlanRenders.add(element.elementId)) {
      debugPrint(
        '[BlackboardView] renderer received valid drawing plan element=${element.elementId} elements=${plan.elements.length} stages=${plan.stages.length}',
      );
    }
    final result = GenerativeDrawingRenderer.render(
      canvas,
      plan,
      transform,
      progress: progress,
    );
    for (final entry in result.elementBounds.entries) {
      bounds.put(entry.key, entry.value);
    }
    // Also register the overall plan bounds for the board element id.
    final boxes = result.elementBounds.values.toList(growable: false);
    if (boxes.isNotEmpty) {
      var union = boxes.first;
      for (final b in boxes.skip(1)) {
        union = union.union(b);
      }
      bounds.put(element.elementId, union.clamp01());
    }
  }

  void _strokePath(
    ui.Canvas canvas,
    Path path,
    Color color,
    double strokeWidth, {
    List<double>? dash,
    double progress = 1,
  }) {
    final effectivePath =
        progress < 1 ? ChalkGeometry.revealPath(path, progress) : path;
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
      old.transform != transform ||
      old.narrow != narrow ||
      !_sameReflow(old.reflow, reflow);
}

bool _sameReflow(Map<String, BoardPoint> a, Map<String, BoardPoint> b) {
  if (a.length != b.length) return false;
  for (final entry in a.entries) {
    if (b[entry.key] != entry.value) return false;
  }
  return true;
}
