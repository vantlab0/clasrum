import 'dart:math' as math;

import '../model/board_geometry.dart';

/// Pure measurement of how much of the student's visible board is covered.
///
/// The backend owns physical placement and snapshots; this detector only
/// answers "is the visible screen full?" using measured renderer bounds. For
/// virtual scroll boards, coverage is measured against the visible viewport;
/// reaching the bottom of scrollable content is reported separately and does
/// not count as a filled board by itself.
class BoardFullnessSnapshot {
  const BoardFullnessSnapshot({
    required this.visibleRect,
    required this.coverage,
    required this.intersectingElementCount,
    required this.isFull,
    required this.sequence,
    required this.contentExtent,
    required this.reachedBottom,
  });

  final BoardRect visibleRect;
  final double coverage;
  final int intersectingElementCount;
  final bool isFull;
  final int sequence;
  final double contentExtent;
  final bool reachedBottom;

  Map<String, dynamic> toJson() => <String, dynamic>{
        'x': visibleRect.x,
        'y': visibleRect.y,
        'w': visibleRect.w,
        'h': visibleRect.h,
        'content_extent': contentExtent,
        'reached_bottom': reachedBottom,
      };
}

class BoardFullnessDetector {
  const BoardFullnessDetector._();

  /// Covered fraction that counts as a full visible screen.
  static const double fullThreshold = 0.85;

  /// Hysteresis: stay latched until coverage drops below this value.
  static const double releaseThreshold = 0.65;

  /// One oversized element is not a filled lesson board.
  static const int minimumElements = 3;

  static BoardFullnessSnapshot snapshot({
    required BoardRect visibleRect,
    required Iterable<BoardRect> elementBounds,
    required int sequence,
    bool wasFull = false,
    double contentExtent = 1.0,
  }) {
    final extent = math.max(1.0, contentExtent);
    final visible = visibleRect.clampToExtent(extent);
    final reachedBottom =
        visible.bottom >= extent - 0.001 || extent <= 1.0001;
    if (visible.isEmpty) {
      return BoardFullnessSnapshot(
        visibleRect: visible,
        coverage: 0,
        intersectingElementCount: 0,
        isFull: false,
        sequence: sequence,
        contentExtent: extent,
        reachedBottom: reachedBottom,
      );
    }

    final clipped = <BoardRect>[];
    for (final bounds in elementBounds) {
      final clamped = bounds.clampToExtent(extent);
      final left = clamped.x > visible.x ? clamped.x : visible.x;
      final top = clamped.y > visible.y ? clamped.y : visible.y;
      final right =
          clamped.right < visible.right ? clamped.right : visible.right;
      final bottom =
          clamped.bottom < visible.bottom ? clamped.bottom : visible.bottom;
      if (right > left && bottom > top) {
        clipped.add(BoardRect(left, top, right - left, bottom - top));
      }
    }

    final coveredArea = _coveredArea(visible, clipped);
    final denom = (visible.w * visible.h);
    final coverage =
        denom <= 0 ? 0.0 : (coveredArea / denom).clamp(0.0, 1.0);
    final enoughElements = clipped.length >= minimumElements;
    final isFull = wasFull
        ? coverage >= releaseThreshold
        : coverage >= fullThreshold && enoughElements;
    return BoardFullnessSnapshot(
      visibleRect: visible,
      coverage: coverage,
      intersectingElementCount: clipped.length,
      isFull: isFull,
      sequence: sequence,
      contentExtent: extent,
      reachedBottom: reachedBottom,
    );
  }

  /// Union area of [boxes] inside [visible], computed by coordinate compression
  /// so overlapping boxes are never counted twice.
  static double _coveredArea(BoardRect visible, List<BoardRect> boxes) {
    if (boxes.isEmpty) return 0.0;
    final xs = <double>{visible.x, visible.right};
    final ys = <double>{visible.y, visible.bottom};
    for (final box in boxes) {
      xs.add(box.x);
      xs.add(box.right);
      ys.add(box.y);
      ys.add(box.bottom);
    }
    final xValues = xs.toList()..sort();
    final yValues = ys.toList()..sort();
    var area = 0.0;
    for (var xi = 0; xi < xValues.length - 1; xi++) {
      final x0 = xValues[xi];
      final x1 = xValues[xi + 1];
      if (x1 <= x0) continue;
      final midX = (x0 + x1) / 2.0;
      for (var yi = 0; yi < yValues.length - 1; yi++) {
        final y0 = yValues[yi];
        final y1 = yValues[yi + 1];
        if (y1 <= y0) continue;
        final midY = (y0 + y1) / 2.0;
        final point = BoardPoint(midX, midY);
        for (final box in boxes) {
          if (box.containsPoint(point)) {
            area += (x1 - x0) * (y1 - y0);
            break;
          }
        }
      }
    }
    return area;
  }
}
