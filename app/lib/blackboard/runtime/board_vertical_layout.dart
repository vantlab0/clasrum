import 'dart:math' as math;
import 'dart:ui' show Size;

import '../model/board_document.dart';
import '../model/board_element.dart';
import '../model/board_event.dart';
import '../model/board_geometry.dart';

/// Measured normalized bounds for an element. Text uses the chalk engine's
/// `TextPainter` result; everything else uses the render cache or estimator.
typedef BoardMeasuredBounds = BoardRect? Function(BoardElement element);

/// Client-side vertical reading mode for narrow (phone portrait) boards.
///
/// The backend still owns authoritative placement and snapshots. This is a purely
/// visual reflow of the same document: elements are sorted by their original
/// `y`, stacked top-to-bottom using measured heights, and lightly shifted in x
/// so nothing sits half off the screen. The document/sequence is never changed,
/// so replay and the server layout stay untouched.
class BoardVerticalLayout {
  const BoardVerticalLayout._();

  /// Portrait-ish viewports get vertical reading mode.
  static const double aspectRatioThreshold = 0.8;

  static const double sideMargin = 0.05; // ~90% content width on mobile
  static const double topMargin = 0.05;
  static const double blockGap = 0.018;

  static bool isNarrow(Size size) =>
      size.height > 0 && size.width / size.height < aspectRatioThreshold;

  /// Returns normalized target positions keyed by element id. An empty map means
  /// "use the authoritative position" (desktop or nothing to reflow).
  static Map<String, BoardPoint> positions({
    required BoardDocument document,
    required BoardMeasuredBounds boundsOf,
    required bool narrow,
  }) {
    if (!narrow) return const <String, BoardPoint>{};

    final candidates = <BoardElement>[];
    for (final element in document.visibleElements) {
      if (!_isBlockContent(element)) continue;
      // Plots and labels belong to a coordinate-system block and travel with it.
      final parentId = element.parentId;
      if (parentId != null && parentId.isNotEmpty && document[parentId] != null) {
        continue;
      }
      candidates.add(element);
    }
    if (candidates.isEmpty) return const <String, BoardPoint>{};

    final grouped = <String, List<BoardElement>>{};
    final blocks = <_Block>[];
    for (final element in candidates) {
      final groupId = element.groupId;
      if (groupId != null && groupId.isNotEmpty) {
        grouped.putIfAbsent(groupId, () => <BoardElement>[]).add(element);
      } else {
        blocks.add(_makeBlock(<BoardElement>[element], boundsOf));
      }
    }
    for (final members in grouped.values) {
      blocks.add(_makeBlock(members, boundsOf));
    }
    blocks.sort((a, b) {
      final order = a.top.compareTo(b.top);
      return order != 0 ? order : a.left.compareTo(b.left);
    });

    final positions = <String, BoardPoint>{};
    const maxWidth = 1.0 - 2 * sideMargin;
    var cursor = topMargin;
    for (final block in blocks) {
      // Keep stacking even past the visible bottom: an overflowing stack is a
      // truthful signal for the board-fullness detector to recycle the board.
      final width = block.width.clamp(0.08, maxWidth);
      // Keep a sensible original x when it already fits; otherwise left-align.
      var x = block.left;
      if (x < sideMargin || x + width > 1.0 - sideMargin) {
        x = sideMargin;
      } else {
        x = x.clamp(sideMargin, 1.0 - sideMargin - width);
      }
      for (final element in block.members) {
        final original = element.position ?? BoardPoint.zero;
        positions[element.elementId] = BoardPoint(
          x + (original.x - block.left),
          cursor + (original.y - block.top),
        );
      }
      cursor += math.min(block.height, 1.0) + blockGap;
    }

    // Resolve child offsets after their parent block has moved. A short loop
    // covers nested parents without requiring a topological sort.
    for (var pass = 0; pass < 3; pass++) {
      var changed = false;
      for (final element in document.visibleElements) {
        final parentId = element.parentId;
        if (parentId == null) continue;
        final parentTarget = positions[parentId];
        final parentOriginal = document[parentId]?.position;
        if (parentTarget == null || parentOriginal == null) continue;
        final original = element.position ?? BoardPoint.zero;
        final target = BoardPoint(
          original.x + parentTarget.x - parentOriginal.x,
          original.y + parentTarget.y - parentOriginal.y,
        );
        final previous = positions[element.elementId];
        if (previous == null || previous != target) {
          positions[element.elementId] = target;
          changed = true;
        }
      }
      if (!changed) break;
    }
    return positions;
  }

  static bool _isBlockContent(BoardElement element) {
    if (element.isAnnotation) return false;
    switch (element.kind) {
      case BoardEventType.viewportFocus:
      case BoardEventType.clear:
      case BoardEventType.erase:
      case BoardEventType.eraseElement:
      case BoardEventType.moveElement:
      case BoardEventType.unknown:
        return false;
      default:
        return true;
    }
  }

  static _Block _makeBlock(
    List<BoardElement> members,
    BoardMeasuredBounds boundsOf,
  ) {
    var left = 1.0;
    var top = 1.0;
    var right = 0.0;
    var bottom = 0.0;
    for (final element in members) {
      final original = element.position ?? BoardPoint.zero;
      final measured = boundsOf(element);
      final width = measured?.w ?? element.size?.w ?? 0.2;
      final height = measured?.h ?? element.size?.h ?? 0.08;
      left = math.min(left, original.x);
      top = math.min(top, original.y);
      right = math.max(right, original.x + width);
      bottom = math.max(bottom, original.y + height);
    }
    return _Block(
      members: members,
      top: top,
      left: left,
      width: math.max(0.08, right - left),
      height: math.max(0.03, bottom - top),
    );
  }
}

class _Block {
  const _Block({
    required this.members,
    required this.top,
    required this.left,
    required this.width,
    required this.height,
  });

  final List<BoardElement> members;
  final double top;
  final double left;
  final double width;
  final double height;
}
