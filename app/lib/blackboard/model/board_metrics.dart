import 'dart:math' as math;

import 'board_element.dart';
import 'board_event.dart';
import 'board_geometry.dart';

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
    'heading': 0.056,
    'subheading': 0.043,
    'body': 0.032,
    'definition': 0.032,
    'label': 0.024,
    'caption': 0.021,
    'note': 0.027,
    'code': 0.026,
  };

  /// Mean glyph advance as a fraction of the font size for the bundled chalk
  /// fonts (Patrick Hand is condensed; this is a conservative estimate).
  static const double glyphWidthFactor = 0.54;

  /// Line height multiplier used by both the estimate and the TextPainter.
  static const double lineHeight = 1.28;

  /// Height of a heading of `level` (1 = heading, 2 = subheading).
  static double headingFraction(int level) =>
      level <= 1 ? heightFraction['heading']! : heightFraction['subheading']!;

  static double fractionForStyle(String? style, {int headingLevel = 1}) {
    if (style == 'heading') return headingFraction(headingLevel);
    return heightFraction[style] ?? heightFraction['body']!;
  }

  /// Estimated normalized line height for [style] at 100% font scale.
  static double lineHeightFraction(String? style, {int headingLevel = 1}) =>
      fractionForStyle(style, headingLevel: headingLevel) * lineHeight;
}

/// Pure, deterministic normalized bounds estimation for board elements.
///
/// Used by [BoardDocument.boundsOf] to place highlight annotations when no
/// renderer measurement exists yet (snapshot restored before first paint,
/// replay, tests). The renderer's measured bounds take precedence at paint time.
class BoardBoundsEstimator {
  const BoardBoundsEstimator._();

  /// Conservative element box; never null for a well-formed element.
  static BoardRect? estimate(BoardElement element, {int depth = 0}) {
    final position = element.position;
    final declared = element.declaredBounds;
    if (declared != null && !declared.isEmpty) return declared.clamp01();

    switch (element.kind) {
      case BoardEventType.writeText:
        return _estimateText(element, position);
      case BoardEventType.writeHeading:
        return _estimateHeading(element, position);
      case BoardEventType.writeDefinition:
        return _estimateDefinition(element, position);
      case BoardEventType.writeBulletList:
        return _estimateBulletList(element, position);
      case BoardEventType.writeEquation:
      case BoardEventType.updateEquation:
        return _estimateEquation(element, position);
      case BoardEventType.drawLine:
        return _estimateLine(element);
      case BoardEventType.drawArrow:
        return _estimateArrow(element);
      case BoardEventType.drawShape:
        return _estimateShape(element, position);
      case BoardEventType.drawPath:
        return _estimatePath(element);
      case BoardEventType.drawDiagram:
        return _estimateDiagram(element);
      case BoardEventType.createCoordinateSystem:
        return _estimateCoordinateSystem(element, position);
      case BoardEventType.plotFunction:
      case BoardEventType.plotPoints:
        // The document copies the owning coordinate-system box onto plots.
        return position == null ? null : BoardRect(position.x, position.y, 0.4, 0.3);
      case BoardEventType.addGraphLabel:
        return _estimateGraphLabel(element);
      case BoardEventType.highlight:
      case BoardEventType.highlightEquation:
        return _estimateAnnotation(element);
      default:
        return position == null
            ? null
            : BoardRect(position.x, position.y, element.size?.w ?? 0.2,
                element.size?.h ?? 0.08);
    }
  }

  static BoardRect? _estimateText(BoardElement element, BoardPoint? position) {
    final data = element.payloadParsedOrRaw();
    final text = (data['text']?.toString() ?? '').trim();
    if (position == null || text.isEmpty) return null;
    final style = (data['style']?.toString()) ?? 'body';
    final fontScale = _num(data['font_scale']) ?? 1.0;
    final font = BoardTextMetrics.fractionForStyle(style) * fontScale;
    final maxWidth = _num(data['max_width']);
    final available = maxWidth != null
        ? maxWidth.clamp(0.05, 1.0 - position.x)
        : (1.0 - position.x - 0.02).clamp(0.05, 1.0);
    final naturalWidth =
        math.max(1, text.length) * font * BoardTextMetrics.glyphWidthFactor;
    final width = math.min(naturalWidth, available);
    final lines = math.max(1, (naturalWidth / available).ceil());
    final height = lines * font * BoardTextMetrics.lineHeight;
    return BoardRect(position.x, position.y, width, height).clamp01();
  }

  static BoardRect? _estimateHeading(BoardElement element, BoardPoint? position) {
    if (position == null) return null;
    final data = element.payloadParsedOrRaw();
    final text = data['text']?.toString() ?? '';
    final level = (_num(data['level']) ?? 1).round().clamp(1, 2);
    final font = BoardTextMetrics.headingFraction(level);
    final width = math.min(
      (1.0 - position.x - 0.04).clamp(0.05, 1.0),
      math.max(1, text.length) * font * BoardTextMetrics.glyphWidthFactor,
    );
    final height = font * BoardTextMetrics.lineHeight + (level == 1 ? 0.012 : 0);
    return BoardRect(position.x, position.y, width, height).clamp01();
  }

  static BoardRect? _estimateDefinition(
    BoardElement element,
    BoardPoint? position,
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
      (text.length * font * BoardTextMetrics.glyphWidthFactor /
              math.max(width, 0.1))
          .ceil(),
    );
    final height =
        termHeight + 0.008 + bodyLines * font * BoardTextMetrics.lineHeight;
    return BoardRect(position.x, position.y, width, height).clamp01();
  }

  static BoardRect? _estimateBulletList(
    BoardElement element,
    BoardPoint? position,
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
    final height = items.length * font * BoardTextMetrics.lineHeight * 1.15;
    return BoardRect(position.x, position.y, width, height).clamp01();
  }

  static BoardRect? _estimateEquation(
    BoardElement element,
    BoardPoint? position,
  ) {
    if (position == null) return null;
    final data = element.payloadParsedOrRaw();
    final latex = data['latex']?.toString() ?? '';
    final fontScale = _num(data['font_scale']) ?? 1.0;
    final font = BoardTextMetrics.fractionForStyle('body') * fontScale * 1.9;
    final width = math.min(
      (1.0 - position.x - 0.02).clamp(0.05, 1.0),
      math.max(3, latex.length) * font * 0.32,
    );
    var height = font * 2.2;
    if ((data['caption']?.toString() ?? '').isNotEmpty) {
      height += BoardTextMetrics.fractionForStyle('caption') * 1.8;
    }
    return BoardRect(position.x, position.y, width, height).clamp01();
  }

  static BoardRect? _estimateLine(BoardElement element) {
    final data = element.payloadParsedOrRaw();
    final start = BoardPoint.tryParse(data['start']);
    final end = BoardPoint.tryParse(data['end']);
    if (start == null || end == null) return null;
    return _paddedBounds(<BoardPoint>[start, end], pad: 0.012);
  }

  static BoardRect? _estimateArrow(BoardElement element) => _estimateLine(element);

  static BoardRect? _estimateShape(BoardElement element, BoardPoint? position) {
    final size = element.size;
    if (position != null && size != null) {
      return BoardRect.fromPositionSize(position, size).clamp01();
    }
    final data = element.payloadParsedOrRaw();
    final raw = data['points'];
    if (raw is List) {
      final points = <BoardPoint>[];
      for (final item in raw) {
        final point = BoardPoint.tryParse(item);
        if (point != null) points.add(point);
      }
      if (points.isNotEmpty) return _paddedBounds(points, pad: 0.012);
    }
    return position == null ? null : BoardRect(position.x, position.y, 0.2, 0.14);
  }

  static BoardRect? _estimatePath(BoardElement element) {
    final data = element.payloadParsedOrRaw();
    final raw = data['points'];
    if (raw is! List) return null;
    final points = <BoardPoint>[];
    for (final item in raw) {
      final point = BoardPoint.tryParse(item);
      if (point != null) points.add(point);
    }
    if (points.isEmpty) return null;
    return _paddedBounds(points, pad: 0.014);
  }

  static BoardRect? _estimateDiagram(BoardElement element) {
    final data = element.payloadParsedOrRaw();
    final nodes = data['nodes'];
    if (nodes is! List || nodes.isEmpty) return null;
    var bounds = BoardRect.zero;
    var first = true;
    for (final rawNode in nodes) {
      if (rawNode is! Map) continue;
      final position = BoardPoint.tryParse(rawNode['position']);
      if (position == null) continue;
      final size = BoardSize.tryParse(rawNode['size']) ??
          const BoardSize(0.16, 0.09);
      final nodeRect = BoardRect.fromPositionSize(position, size);
      bounds = first ? nodeRect : bounds.union(nodeRect);
      first = false;
    }
    if (first) return null;
    final title = data['title']?.toString() ?? '';
    final top = title.isEmpty ? bounds.y : bounds.y - 0.045;
    final height = bounds.h + (title.isEmpty ? 0 : 0.045);
    return BoardRect(bounds.x, top, bounds.w, height).clamp01();
  }

  static BoardRect? _estimateCoordinateSystem(
    BoardElement element,
    BoardPoint? position,
  ) {
    final size = element.size ?? const BoardSize(0.5, 0.5);
    if (position == null) return null;
    return BoardRect.fromPositionSize(position, size).clamp01();
  }

  static BoardRect? _estimateGraphLabel(BoardElement element) {
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
        .clamp01();
  }

  static BoardRect? _estimateAnnotation(BoardElement element) {
    final position = element.position;
    final size = element.size;
    if (position != null) {
      final rect = size == null
          ? BoardRect(position.x, position.y, 0.2, 0.08)
          : BoardRect.fromPositionSize(position, size);
      return rect.inflate(0.01).clamp01();
    }
    return null;
  }

  static BoardRect _paddedBounds(List<BoardPoint> points, {required double pad}) {
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
        .clamp01();
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
