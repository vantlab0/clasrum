import 'dart:math' as math;

import 'board_element.dart';
import 'board_event.dart';
import 'board_geometry.dart';
import 'drawing_plan.dart';

/// Normalized text style metrics shared by the document (for deterministic
/// annotation bounds estimates) and the chalk text renderer (as a fallback when
/// no exact measurement is available yet).
///
/// Font sizes are fractions of the board content height, so the board scales
/// with the viewport and orientation.
class BoardTextMetrics {
  const BoardTextMetrics._();

  /// Font size as a fraction of board content height, per CONTRACTS §3.1 style.
  static const Map<String, double> heightFraction = <String, double>{
    // H1-H6 are deliberately distinct: direct board writing preserves the
    // semantic level, so the renderer must not collapse h3..h6 into h2.
    'h1': 0.076,
    'h2': 0.062,
    'h3': 0.054,
    'h4': 0.047,
    'h5': 0.041,
    'h6': 0.036,
    'heading': 0.076,
    'subheading': 0.062,
    'body': 0.032,
    'definition': 0.032,
    'label': 0.024,
    'caption': 0.021,
    'note': 0.027,
    'code': 0.026,
    // Layout-time aliases for the renderer's ChalkTextStyleSpec entries.
    'small': 0.026,
    'equation': 0.038,
    'bullet': 0.031,
    'numbered': 0.031,
    'definitionterm': 0.038,
  };

  /// Mean glyph advance as a fraction of the font size for the bundled chalk
  /// fonts (Patrick Hand is condensed; this is a conservative estimate).
  static const double glyphWidthFactor = 0.54;

  /// Line height multiplier used by both the estimate and the TextPainter.
  static const double lineHeight = 1.28;

  /// Height of a heading of `level` (2..6; legacy level 1 renders as H2).
  static double headingFraction(int level) {
    final safe = level.clamp(2, 6);
    return heightFraction['h$safe'] ?? heightFraction['subheading']!;
  }

  static double fractionForStyle(String? style, {int headingLevel = 2}) {
    final normalized = (style ?? '').toLowerCase();
    if (normalized == 'heading' || normalized == 'subheading') {
      return headingFraction(headingLevel);
    }
    return heightFraction[normalized] ?? heightFraction['body']!;
  }

  /// Line-height multiplier used by the renderer for [style] (code is taller
  /// so monospace glyphs do not clip).
  static double lineHeightForStyle(String? style) =>
      (style ?? '').trim().toLowerCase() == 'code' ? 1.35 : lineHeight;

  /// Estimated normalized line height for [style] at 100% font scale.
  static double lineHeightFraction(String? style, {int headingLevel = 2}) =>
      fractionForStyle(style, headingLevel: headingLevel) *
      lineHeightForStyle(style);
}

/// Pure, deterministic normalized bounds estimation for board elements.
///
/// Used by [BoardDocument.boundsOf] to place highlight annotations when no
/// renderer measurement exists yet (snapshot restored before first paint,
/// replay, tests). The renderer's measured bounds take precedence at paint time.
class BoardBoundsEstimator {
  const BoardBoundsEstimator._();

  /// Conservative element box; never null for a well-formed element.
  static BoardRect? estimate(
    BoardElement element, {
    int depth = 0,
    double verticalExtent = 1.0,
  }) {
    final position = element.position;
    final declared = element.declaredBounds;
    if (declared != null && !declared.isEmpty) {
      return declared.clampToExtent(verticalExtent);
    }

    switch (element.kind) {
      case BoardEventType.writeText:
        return _estimateText(element, position, verticalExtent);
      case BoardEventType.writeHeading:
        return _estimateHeading(element, position, verticalExtent);
      case BoardEventType.writeDefinition:
        return _estimateDefinition(element, position, verticalExtent);
      case BoardEventType.writeBulletList:
        return _estimateBulletList(element, position, verticalExtent);
      case BoardEventType.writeEquation:
      case BoardEventType.updateEquation:
        return _estimateEquation(element, position, verticalExtent);
      case BoardEventType.drawLine:
        return _estimateLine(element, verticalExtent);
      case BoardEventType.drawArrow:
        return _estimateArrow(element, verticalExtent);
      case BoardEventType.drawShape:
        return _estimateShape(element, position, verticalExtent);
      case BoardEventType.drawPath:
        return _estimatePath(element, verticalExtent);
      case BoardEventType.drawDiagram:
        return _estimateDiagram(element, verticalExtent);
      case BoardEventType.drawPlan:
        return _estimateDrawPlan(element, verticalExtent);
      case BoardEventType.createCoordinateSystem:
        return _estimateCoordinateSystem(element, position, verticalExtent);
      case BoardEventType.plotFunction:
      case BoardEventType.plotPoints:
        // The document copies the owning coordinate-system box onto plots.
        return position == null
            ? null
            : BoardRect(position.x, position.y, 0.4, 0.3);
      case BoardEventType.addGraphLabel:
        return _estimateGraphLabel(element, verticalExtent);
      case BoardEventType.highlight:
      case BoardEventType.highlightEquation:
        return _estimateAnnotation(element, verticalExtent);
      default:
        return position == null
            ? null
            : BoardRect(position.x, position.y, element.size?.w ?? 0.2,
                element.size?.h ?? 0.08)
                .clampToExtent(verticalExtent);
    }
  }

  static BoardRect? _estimateText(
      BoardElement element, BoardPoint? position, double verticalExtent) {
    final data = element.payloadParsedOrRaw();
    final text = (data['text']?.toString() ?? '').trim();
    if (position == null || text.isEmpty) return null;
    final style = (data['style']?.toString()) ?? 'body';
    final headingLevel = _headingLevel(data);
    final fontScale = _num(data['font_scale']) ?? 1.0;
    final font = BoardTextMetrics.fractionForStyle(
          style,
          headingLevel: headingLevel,
        ) *
        fontScale;
    final maxWidth = _num(data['max_width']);
    final available = maxWidth != null
        ? maxWidth.clamp(0.05, 1.0 - position.x)
        : (1.0 - position.x - 0.02).clamp(0.05, 1.0);
    final naturalWidth =
        math.max(1, text.length) * font * BoardTextMetrics.glyphWidthFactor;
    final width = math.min(naturalWidth, available);
    final lines = math.max(1, (naturalWidth / available).ceil());
    final height =
        lines * font * BoardTextMetrics.lineHeightForStyle(style);
    return BoardRect(position.x, position.y, width, height).clampToExtent(verticalExtent);
  }

  static BoardRect? _estimateHeading(
      BoardElement element, BoardPoint? position, double verticalExtent) {
    if (position == null) return null;
    final data = element.payloadParsedOrRaw();
    final text = data['text']?.toString() ?? '';
    final level = _headingLevel(data);
    final font = BoardTextMetrics.headingFraction(level);
    final width = math.min(
      (1.0 - position.x - 0.04).clamp(0.05, 1.0),
      math.max(1, text.length) * font * BoardTextMetrics.glyphWidthFactor,
    );
    final height =
        font * BoardTextMetrics.lineHeight + (level <= 2 ? 0.012 : 0);
    return BoardRect(position.x, position.y, width, height).clampToExtent(verticalExtent);
  }

  /// Reads the semantic heading level emitted by the backend layout engine.
  /// Legacy H1 events are normalized to H2.
  static int _headingLevel(Map<String, dynamic> data) {
    final raw = data['heading_level'] ?? data['level'] ?? data['style'];
    if (raw is String && raw.length == 2 && raw[0] == 'h') {
      return (int.tryParse(raw[1]) ?? 2).clamp(2, 6);
    }
    return (_num(raw) ?? 2).round().clamp(2, 6);
  }

  static BoardRect? _estimateDefinition(
    BoardElement element,
    BoardPoint? position,
    double verticalExtent,
  ) {
    if (position == null) return null;
    final data = element.payloadParsedOrRaw();
    final term = data['term']?.toString() ?? '';
    final text = data['text']?.toString() ?? '';
    final font = BoardTextMetrics.fractionForStyle('definition');
    final termFont = BoardTextMetrics.fractionForStyle('subheading');
    final width = math.min(
      (1.0 - position.x - 0.03).clamp(0.05, 1.0),
      math.max(term.length, text.length) *
          font *
          BoardTextMetrics.glyphWidthFactor,
    );
    final termHeight = termFont * BoardTextMetrics.lineHeight;
    final bodyLines = math.max(
      1,
      (text.length *
              font *
              BoardTextMetrics.glyphWidthFactor /
              math.max(width, 0.1))
          .ceil(),
    );
    final height =
        termHeight + 0.008 + bodyLines * font * BoardTextMetrics.lineHeight;
    return BoardRect(position.x, position.y, width, height).clampToExtent(verticalExtent);
  }

  static BoardRect? _estimateBulletList(
    BoardElement element,
    BoardPoint? position,
    double verticalExtent,
  ) {
    if (position == null) return null;
    final data = element.payloadParsedOrRaw();
    final items = (data['items'] as List?) ?? const <dynamic>[];
    if (items.isEmpty) return null;
    final font = BoardTextMetrics.fractionForStyle('body');
    var widest = 0.0;
    for (final item in items) {
      widest = math.max(
        widest,
        item.toString().length * font * BoardTextMetrics.glyphWidthFactor,
      );
    }
    final width = math.min(
      (1.0 - position.x - 0.08).clamp(0.05, 1.0),
      widest + font,
    );
    final height =
        items.length * font * BoardTextMetrics.lineHeightForStyle('bullet') * 1.15;
    return BoardRect(position.x, position.y, width, height).clampToExtent(verticalExtent);
  }

  static BoardRect? _estimateEquation(
    BoardElement element,
    BoardPoint? position,
    double verticalExtent,
  ) {
    if (position == null) return null;
    final data = element.payloadParsedOrRaw();
    final latex = data['latex']?.toString() ?? '';
    final fontScale = _num(data['font_scale']) ?? 1.0;
    final font =
        BoardTextMetrics.fractionForStyle('equation') * fontScale;
    final declaredWidth = element.size?.w ?? _num(data['max_width']);
    final available = declaredWidth ??
        (1.0 - position.x - 0.02).clamp(0.05, 1.0);
    final naturalWidth = math.max(3, latex.length) * font * 0.62;
    final width = math.min(available.clamp(0.05, 1.0), naturalWidth);
    final lines = math.max(1, (naturalWidth / math.max(width, 0.05)).ceil());
    var height = element.size?.h ??
        lines * font * BoardTextMetrics.lineHeightForStyle('equation') * 2.4;
    if ((data['caption']?.toString() ?? '').isNotEmpty) {
      height += BoardTextMetrics.fractionForStyle('caption') * 1.8;
    }
    return BoardRect(position.x, position.y, width, height).clampToExtent(verticalExtent);
  }

  static BoardRect? _estimateLine(BoardElement element, double verticalExtent) {
    final data = element.payloadParsedOrRaw();
    final start = BoardPoint.tryParse(data['start']);
    final end = BoardPoint.tryParse(data['end']);
    if (start == null || end == null) return null;
    return _paddedBounds(<BoardPoint>[start, end], verticalExtent, pad: 0.012);
  }

  static BoardRect? _estimateArrow(BoardElement element, double verticalExtent) =>
      _estimateLine(element, verticalExtent);

  static BoardRect? _estimateShape(
      BoardElement element, BoardPoint? position, double verticalExtent) {
    final size = element.size;
    if (position != null && size != null) {
      return BoardRect.fromPositionSize(position, size).clampToExtent(verticalExtent);
    }
    final data = element.payloadParsedOrRaw();
    final raw = data['points'];
    if (raw is List) {
      final points = <BoardPoint>[];
      for (final item in raw) {
        final point = BoardPoint.tryParse(item);
        if (point != null) points.add(point);
      }
      if (points.isNotEmpty) return _paddedBounds(points, verticalExtent, pad: 0.012);
    }
    return position == null
        ? null
        : BoardRect(position.x, position.y, 0.2, 0.14);
  }

  static BoardRect? _estimatePath(BoardElement element, double verticalExtent) {
    final data = element.payloadParsedOrRaw();
    final raw = data['points'];
    if (raw is! List) return null;
    final points = <BoardPoint>[];
    for (final item in raw) {
      final point = BoardPoint.tryParse(item);
      if (point != null) points.add(point);
    }
    if (points.isEmpty) return null;
    return _paddedBounds(points, verticalExtent, pad: 0.014);
  }

  static BoardRect? _estimateDiagram(BoardElement element, double verticalExtent) {
    final data = element.payloadParsedOrRaw();
    final nodes = data['nodes'];
    if (nodes is! List || nodes.isEmpty) return null;
    var bounds = BoardRect.zero;
    var first = true;
    for (final rawNode in nodes) {
      if (rawNode is! Map) continue;
      final position = BoardPoint.tryParse(rawNode['position']);
      if (position == null) continue;
      final size =
          BoardSize.tryParse(rawNode['size']) ?? const BoardSize(0.16, 0.09);
      final nodeRect = BoardRect.fromPositionSize(position, size);
      bounds = first ? nodeRect : bounds.union(nodeRect);
      first = false;
    }
    if (first) return null;
    final title = data['title']?.toString() ?? '';
    final top = title.isEmpty ? bounds.y : bounds.y - 0.045;
    final height = bounds.h + (title.isEmpty ? 0 : 0.045);
    return BoardRect(bounds.x, top, bounds.w, height).clampToExtent(verticalExtent);
  }

  static BoardRect? _estimateDrawPlan(BoardElement element, double verticalExtent) {
    final plan = DrawingPlan.tryParse(element.payload);
    if (plan == null) return null;
    final boxes = plan.elementBounds;
    if (boxes.isEmpty) return null;
    final position = element.position;
    final size = element.size;
    final region = position == null || size == null
        ? null
        : BoardRect.fromPositionSize(position, size);
    BoardRect map(BoardRect box) => region == null
        ? box
        : BoardRect(
            region.x + box.x * region.w,
            region.y + box.y * region.h,
            box.w * region.w,
            box.h * region.h,
          );
    var union = map(boxes.first);
    for (final box in boxes.skip(1)) {
      union = union.union(map(box));
    }
    return union.clampToExtent(verticalExtent);
  }

  static BoardRect? _estimateCoordinateSystem(
    BoardElement element,
    BoardPoint? position,
    double verticalExtent,
  ) {
    final size = element.size ?? const BoardSize(0.5, 0.5);
    if (position == null) return null;
    return BoardRect.fromPositionSize(position, size).clampToExtent(verticalExtent);
  }

  static BoardRect? _estimateGraphLabel(BoardElement element, double verticalExtent) {
    final data = element.payloadParsedOrRaw();
    final at = BoardPoint.tryParse(data['at']);
    final text = data['text']?.toString() ?? '';
    if (at == null) return null;
    final font = BoardTextMetrics.fractionForStyle('label');
    final width = math.min(
      (1.0 - at.x).clamp(0.05, 1.0),
      math.max(1, text.length) * font * BoardTextMetrics.glyphWidthFactor,
    );
    return BoardRect(at.x, at.y, width, font * BoardTextMetrics.lineHeight)
        .clampToExtent(verticalExtent);
  }

  static BoardRect? _estimateAnnotation(BoardElement element, double verticalExtent) {
    final position = element.position;
    final size = element.size;
    if (position != null) {
      final rect = size == null
          ? BoardRect(position.x, position.y, 0.2, 0.08)
          : BoardRect.fromPositionSize(position, size);
      return rect.inflate(0.01).clampToExtent(verticalExtent);
    }
    return null;
  }

  static BoardRect _paddedBounds(
    List<BoardPoint> points,
    double verticalExtent, {
    double pad = 0.01,
  }) {
    var minX = points.first.x;
    var minY = points.first.y;
    var maxX = minX;
    var maxY = minY;
    for (final point in points) {
      minX = math.min(minX, point.x);
      minY = math.min(minY, point.y);
      maxX = math.max(maxX, point.x);
      maxY = math.max(maxY, point.y);
    }
    return BoardRect(minX - pad, minY - pad, maxX - minX + pad * 2,
            maxY - minY + pad * 2)
        .clampToExtent(verticalExtent);
  }

  static double? _num(Object? value) {
    if (value is num) return value.toDouble();
    if (value is String) return double.tryParse(value);
    return null;
  }
}

/// Convenience used by the estimator to read the raw payload without importing
/// the concrete payload DTO classes.
extension PayloadRawAccess on BoardElement {
  Map<String, dynamic> payloadParsedOrRaw() => payload;
}
