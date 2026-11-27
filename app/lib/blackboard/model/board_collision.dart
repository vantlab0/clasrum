import 'board_element.dart';
import 'board_geometry.dart';
import 'board_metrics.dart';

/// Why two element boxes overlap.
///
/// Only [unrelated] is a layout defect. Overlaps inside one authored group,
/// between a parent and its child, between elements created by the same event,
/// and between an annotation and its target are deliberate.
enum BoardCollisionKind {
  unrelated,
  sameGroup,
  parentChild,
  sameEvent,
  annotationTarget,
}

/// A detected overlap between two live board elements.
class BoardCollision {
  const BoardCollision({
    required this.first,
    required this.second,
    required this.overlap,
    required this.kind,
    required this.reason,
  });

  final BoardElement first;
  final BoardElement second;
  final BoardRect overlap;
  final BoardCollisionKind kind;
  final String reason;

  bool get isIntentional => kind != BoardCollisionKind.unrelated;

  @override
  String toString() =>
      'BoardCollision(${first.elementId}, ${second.elementId}, $kind)';
}

/// Group-aware collision checks for the Flutter document.
///
/// The backend layout manager is authoritative for positions: this detector
/// never moves anything. It validates replayed/transmitted positions and lets
/// the UI distinguish a real layout defect from intentional composite artwork.
class BoardCollisionDetector {
  const BoardCollisionDetector._();

  /// Mirrors the backend `LayoutConfig.element_gap`. Unrelated elements must
  /// keep at least this much empty chalk between them, including on mobile.
  static const double minimumGap = 0.014;

  /// Best available normalized bounds for [element].
  static BoardRect? boundsOf(BoardElement element) {
    final declared = element.declaredBounds;
    if (declared != null && !declared.isEmpty) return declared.clamp01();
    return BoardBoundsEstimator.estimate(element)?.clamp01();
  }

  /// Checks one pair. Returns null when either element is absent/invisible or
  /// their boxes do not intersect.
  static BoardCollision? between(
    BoardElement a,
    BoardElement b, {
    BoardRect? aBounds,
    BoardRect? bBounds,
    double gap = minimumGap,
  }) {
    if (!a.isVisible || !b.isVisible || a.elementId == b.elementId) return null;
    final first = aBounds ?? boundsOf(a);
    final second = bBounds ?? boundsOf(b);
    if (first == null || second == null) return null;
    // Expand only for the test so a too-small gap is reported as a collision
    // even when the painted boxes themselves do not intersect.
    final paddedFirst = _withGap(first, gap);
    final paddedSecond = _withGap(second, gap);
    if (!paddedFirst.intersects(paddedSecond)) return null;
    final left = first.x > second.x ? first.x : second.x;
    final top = first.y > second.y ? first.y : second.y;
    final right = first.right < second.right ? first.right : second.right;
    final bottom = first.bottom < second.bottom ? first.bottom : second.bottom;
    final overlap = BoardRect(
      left,
      top,
      right > left ? right - left : 0,
      bottom > top ? bottom - top : 0,
    );
    final relationship = relationshipOf(a, b);
    return BoardCollision(
      first: a,
      second: b,
      overlap: overlap,
      kind: relationship.kind,
      reason: relationship.reason,
    );
  }

  /// Mirrors the backend layout manager's relationship rules so client-side
  /// validation agrees with server-side allocation.
  static ({BoardCollisionKind kind, String reason}) relationshipOf(
    BoardElement a,
    BoardElement b,
  ) {
    if (_targetsOf(a).contains(b.elementId) ||
        _targetsOf(b).contains(a.elementId)) {
      return (
        kind: BoardCollisionKind.annotationTarget,
        reason: 'annotation over its target',
      );
    }
    if (a.groupId != null && a.groupId == b.groupId) {
      return (
        kind: BoardCollisionKind.sameGroup,
        reason: 'same group ${a.groupId}',
      );
    }
    if (a.parentId != null &&
        (a.parentId == b.elementId || a.parentId == b.groupId)) {
      return (
        kind: BoardCollisionKind.parentChild,
        reason: '${b.elementId} is parent of ${a.elementId}',
      );
    }
    if (b.parentId != null &&
        (b.parentId == a.elementId || b.parentId == a.groupId)) {
      return (
        kind: BoardCollisionKind.parentChild,
        reason: '${a.elementId} is parent of ${b.elementId}',
      );
    }
    if (a.createdSequence > 0 && a.createdSequence == b.createdSequence) {
      return (
        kind: BoardCollisionKind.sameEvent,
        reason: 'both created by sequence ${a.createdSequence}',
      );
    }
    return (
      kind: BoardCollisionKind.unrelated,
      reason: 'unrelated elements',
    );
  }

  /// Finds overlaps in [elements]. When [focusIds] is supplied only pairs
  /// involving one of those ids are checked (used by the reducer for the event
  /// it just applied).
  static List<BoardCollision> detect(
    Iterable<BoardElement> elements, {
    Iterable<String>? focusIds,
    double gap = minimumGap,
  }) {
    final list = elements.where((element) => element.isVisible).toList();
    final focus = focusIds?.toSet();
    final collisions = <BoardCollision>[];
    for (var i = 0; i < list.length; i++) {
      for (var j = i + 1; j < list.length; j++) {
        if (focus != null &&
            !focus.contains(list[i].elementId) &&
            !focus.contains(list[j].elementId)) {
          continue;
        }
        final collision = between(list[i], list[j], gap: gap);
        if (collision != null) collisions.add(collision);
      }
    }
    return collisions;
  }

  /// Convenience filter used by the document/controller for warnings.
  static List<BoardCollision> unrelated(
    Iterable<BoardElement> elements, {
    Iterable<String>? focusIds,
    double gap = minimumGap,
  }) =>
      detect(elements, focusIds: focusIds, gap: gap)
          .where((collision) => !collision.isIntentional)
          .toList(growable: false);

  static BoardRect _withGap(BoardRect rect, double gap) {
    final half = gap / 2.0;
    return BoardRect(
      rect.x - half,
      rect.y - half,
      rect.w + gap,
      rect.h + gap,
    );
  }

  static Set<String> _targetsOf(BoardElement element) {
    final result = <String>{};
    final raw = element.payload['target_ids'];
    if (raw is List) {
      for (final value in raw) {
        if (value != null) result.add(value.toString());
      }
    }
    final single = element.payload['target_id'];
    if (single != null) result.add(single.toString());
    return result;
  }
}
