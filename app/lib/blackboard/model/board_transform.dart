import 'dart:math' as math;
import 'dart:ui' show Offset, Rect, Size;

import 'board_geometry.dart';

/// Maps normalized board coordinates (0..1, origin top-left) to logical pixels
/// inside one padded content rect. The single transform used by every renderer
/// and by hit testing — never duplicate viewport math elsewhere.
class BoardTransform {
  BoardTransform._(this.size, this.contentRect);

  /// Builds a transform for [size] with a proportional padding (content inset)
  /// so chalk never touches the physical board edge. The board occupies the
  /// whole widget; only this inset lives inside it (no app chrome).
  factory BoardTransform.fromSize(
    Size size, {
    double insetFactor = 0.03,
    double minInset = 10,
    double topExtraInset = 0,
  }) {
    final width = size.width.isFinite && size.width > 0 ? size.width : 1.0;
    final height = size.height.isFinite && size.height > 0 ? size.height : 1.0;
    if (size.width <= 0 || size.height <= 0) {
      // Degenerate constraints (first layout pass / zero-size test pump).
      return BoardTransform._(
        Size(width, height),
        Rect.fromLTWH(0, 0, width, height),
      );
    }
    final inset = math.max(minInset, math.min(width, height) * insetFactor);
    final rect = Rect.fromLTRB(
      inset,
      inset + topExtraInset,
      math.max(inset + 1, width - inset),
      math.max(inset + 1, height - inset),
    );
    return BoardTransform._(Size(width, height), rect);
  }

  final Size size;

  /// The board drawing area in logical pixels (screen space).
  final Rect contentRect;

  double get width => contentRect.width;
  double get height => contentRect.height;

  /// Short edge of the content rect; used to scale jitter/strokes so the board
  /// looks identical in portrait and landscape.
  double get unit => math.min(width, height);

  double sx(double normalizedX) => contentRect.left + normalizedX * width;

  double sy(double normalizedY) => contentRect.top + normalizedY * height;

  Offset toPixels(BoardPoint point) => Offset(sx(point.x), sy(point.y));

  Offset offset(double normalizedX, double normalizedY) =>
      Offset(sx(normalizedX), sy(normalizedY));

  Size sizeToPixels(BoardSize boardSize) =>
      Size(boardSize.w * width, boardSize.h * height);

  Rect rectToPixels(BoardRect rect) => Rect.fromLTWH(
        sx(rect.x),
        sy(rect.y),
        rect.w * width,
        rect.h * height,
      );

  BoardPoint toNormalized(Offset pixels) => BoardPoint(
        (pixels.dx - contentRect.left) / width,
        (pixels.dy - contentRect.top) / height,
      );

  BoardRect normalizedRect(Rect pixels) => BoardRect(
        (pixels.left - contentRect.left) / width,
        (pixels.top - contentRect.top) / height,
        pixels.width / width,
        pixels.height / height,
      );

  /// Font size in pixels for a style expressed as a board-height fraction
  /// (so text scales with the viewport and stays readable in both orientations).
  double fontPixels(double heightFraction, {double fontScale = 1}) =>
      height * heightFraction * fontScale;

  /// Stroke width in logical pixels (CONTRACTS §4 bounds stroke widths 0.5..4).
  double strokePixels(double? strokeWidth) =>
      (strokeWidth ?? 1.6).clamp(0.5, 6.0);

  /// Deterministic chalk jitter in logical pixels, normalized by the board unit.
  double jitterPixels(double normalizedJitter) => normalizedJitter * unit;

  bool containsNormalized(BoardPoint point) =>
      point.x >= 0 && point.x <= 1 && point.y >= 0 && point.y <= 1;

  double distanceNormalized(BoardPoint a, BoardPoint b) {
    final dx = (a.x - b.x) * width;
    final dy = (a.y - b.y) * height;
    return math.sqrt(dx * dx + dy * dy);
  }

  BoardTransform copyWithSize(Size newSize, {double insetFactor = 0.03}) =>
      BoardTransform.fromSize(newSize, insetFactor: insetFactor);

  @override
  bool operator ==(Object other) =>
      other is BoardTransform &&
      other.size == size &&
      other.contentRect == contentRect;

  @override
  int get hashCode => Object.hash(size, contentRect);

  @override
  String toString() => 'BoardTransform(size=$size, content=$contentRect)';
}
