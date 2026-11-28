import 'dart:math' as math;
import 'dart:ui' show Offset, Rect, Size;

import 'board_geometry.dart';

/// Maps normalized board coordinates (0..1 horizontally, 0..logicalExtent
/// vertically, origin top-left) to logical pixels inside one padded content
/// rect. The single transform used by every renderer and by hit testing — never
/// duplicate viewport math elsewhere.
///
/// Supports an optional viewport [offset] (normalized board coords of the
/// visible area's top-left corner) and [scale] (zoom factor) so the board can
/// be panned and zoomed without changing the underlying content layout.
/// [logicalExtent] is the authoritative virtual vertical scroll extent; it
/// defaults to 1.0 for a single-screen board and grows as content extends below
/// the first screen.
class BoardTransform {
  BoardTransform._(
    this.size,
    this.contentRect, {
    this.offset,
    this.scale,
    double logicalExtent = 1.0,
  }) : logicalExtent = math.max(1.0, logicalExtent);

  /// Builds a transform that paints normalized `0..1` coordinates into
  /// [region] of [base]. Used by generated drawing plans, whose payload
  /// coordinates are plan-local while the board element owns the placement.
  factory BoardTransform.forRegion(BoardTransform base, BoardRect region) {
    final scale = base.viewportScale;
    final offset = base.viewportOffset;
    final left = base.contentRect.left +
        (region.x - offset.dx) * base.width * scale;
    final top = base.contentRect.top +
        (region.y - offset.dy) * base.height * scale;
    return BoardTransform._(
      base.size,
      Rect.fromLTWH(
        left,
        top,
        region.w * base.width * scale,
        region.h * base.height * scale,
      ),
      scale: 1.0,
      logicalExtent: base.logicalExtent,
    );
  }

  /// Builds a transform for [size] with a proportional padding (content inset)
  /// so chalk never touches the physical board edge. The board occupies the
  /// whole widget; only this inset lives inside it (no app chrome).
  factory BoardTransform.fromSize(
    Size size, {
    double insetFactor = 0.03,
    double minInset = 10,
    double topExtraInset = 0,
    double logicalExtent = 1.0,
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
    return BoardTransform._(
      Size(width, height),
      rect,
      logicalExtent: logicalExtent,
    );
  }

  final Size size;

  /// The board drawing area in logical pixels (screen space), before viewport
  /// offset/scale is applied.
  final Rect contentRect;

  /// Normalized board-coordinate offset of the visible area's top-left corner.
  /// Offset (0,0) with scale 1.0 shows the full board content rect.
  final Offset? offset;

  /// Zoom scale (1.0 = no zoom). Values > 1 zoom in; < 1 zoom out.
  final double? scale;

  /// Virtual vertical board extent in normalized board units. 1.0 is a single
  /// screen; larger values mean the board scrolls vertically.
  final double logicalExtent;

  double get width => contentRect.width;
  double get height => contentRect.height;

  /// Short edge of the content rect; used to scale jitter/strokes so the board
  /// looks identical in portrait and landscape.
  double get unit => math.min(width, height);

  /// Effective viewport offset, defaulting to zero.
  Offset get viewportOffset => offset ?? Offset.zero;

  /// Effective viewport scale, defaulting to 1.0.
  double get viewportScale => scale ?? 1.0;

  /// Visible height in normalized board units at the current scale.
  double get visibleHeight => (1.0 / viewportScale).clamp(0.0, logicalExtent);

  /// Visible width in normalized board units at the current scale.
  double get visibleWidth => (1.0 / viewportScale).clamp(0.0, 1.0);

  /// Clamps [offset] so the visible viewport stays within the virtual board.
  static Offset clampOffset(
    Offset offset, {
    required double scale,
    required double logicalExtent,
  }) {
    final extent = math.max(1.0, logicalExtent);
    final visibleW = (1.0 / scale).clamp(0.0, 1.0);
    final visibleH = (1.0 / scale).clamp(0.0, extent);
    final maxX = (1.0 - visibleW).clamp(0.0, 1.0);
    final maxY = (extent - visibleH).clamp(0.0, extent);
    return Offset(offset.dx.clamp(0.0, maxX), offset.dy.clamp(0.0, maxY));
  }

  /// The normalized board rect currently visible through the viewport.
  BoardRect get visibleLogicalRect {
    final clamped = clampOffset(
      viewportOffset,
      scale: viewportScale,
      logicalExtent: logicalExtent,
    );
    return BoardRect(clamped.dx, clamped.dy, visibleWidth, visibleHeight);
  }

  /// Pixel X for normalized board X, honoring viewport offset/scale.
  double sx(double normalizedX) {
    return contentRect.left +
        (normalizedX - viewportOffset.dx) * width * viewportScale;
  }

  /// Pixel Y for normalized board Y, honoring viewport offset/scale.
  double sy(double normalizedY) {
    return contentRect.top +
        (normalizedY - viewportOffset.dy) * height * viewportScale;
  }

  Offset toPixels(BoardPoint point) => Offset(sx(point.x), sy(point.y));

  Offset offsetPixels(double normalizedX, double normalizedY) =>
      Offset(sx(normalizedX), sy(normalizedY));

  Size sizeToPixels(BoardSize boardSize) => Size(
      boardSize.w * width * viewportScale,
      boardSize.h * height * viewportScale);

  Rect rectToPixels(BoardRect rect) => Rect.fromLTWH(
        sx(rect.x),
        sy(rect.y),
        rect.w * width * viewportScale,
        rect.h * height * viewportScale,
      );

  BoardPoint toNormalized(Offset pixels) {
    final sx = (pixels.dx - contentRect.left) / (viewportScale * width) +
        viewportOffset.dx;
    final sy = (pixels.dy - contentRect.top) / (viewportScale * height) +
        viewportOffset.dy;
    return BoardPoint(sx, sy);
  }

  BoardRect normalizedRect(Rect pixels) => BoardRect(
        (pixels.left - contentRect.left) / (viewportScale * width) +
            viewportOffset.dx,
        (pixels.top - contentRect.top) / (viewportScale * height) +
            viewportOffset.dy,
        pixels.width / (width * viewportScale),
        pixels.height / (height * viewportScale),
      );

  /// Font size in pixels for a style expressed as a board-height fraction
  /// (so text scales with the viewport and stays readable in both orientations).
  double fontPixels(double heightFraction, {double fontScale = 1}) =>
      height * heightFraction * fontScale * viewportScale;

  /// Stroke width in logical pixels (CONTRACTS §4 bounds stroke widths 0.5..4).
  double strokePixels(double? strokeWidth) =>
      (strokeWidth ?? 1.6).clamp(0.5, 6.0) * viewportScale;

  /// Deterministic chalk jitter in logical pixels, normalized by the board unit.
  double jitterPixels(double normalizedJitter) =>
      normalizedJitter * unit * viewportScale;

  bool containsNormalized(BoardPoint point) =>
      point.x >= 0 &&
      point.x <= 1 &&
      point.y >= 0 &&
      point.y <= logicalExtent;

  double distanceNormalized(BoardPoint a, BoardPoint b) {
    final dx = (a.x - b.x) * width * viewportScale;
    final dy = (a.y - b.y) * height * viewportScale;
    return math.sqrt(dx * dx + dy * dy);
  }

  /// Returns a copy with the given [offset], [scale], and/or [logicalExtent]
  /// applied. Use this to pan/zoom the board without changing content layout.
  BoardTransform withViewport({
    Offset? offset,
    double? scale,
    double? logicalExtent,
  }) {
    return BoardTransform._(
      size,
      contentRect,
      offset: offset ?? this.offset,
      scale: scale ?? this.scale,
      logicalExtent: logicalExtent ?? this.logicalExtent,
    );
  }

  BoardTransform copyWithSize(
    Size newSize, {
    double insetFactor = 0.03,
    double? logicalExtent,
  }) =>
      BoardTransform.fromSize(
        newSize,
        insetFactor: insetFactor,
        logicalExtent: logicalExtent ?? this.logicalExtent,
      );

  @override
  bool operator ==(Object other) =>
      other is BoardTransform &&
      other.size == size &&
      other.contentRect == contentRect &&
      other.offset == offset &&
      other.scale == scale &&
      other.logicalExtent == logicalExtent;

  @override
  int get hashCode =>
      Object.hash(size, contentRect, offset, scale, logicalExtent);

  @override
  String toString() =>
      'BoardTransform(size=$size, content=$contentRect, offset=$offset, '
      'scale=$scale, logicalExtent=$logicalExtent)';
}
