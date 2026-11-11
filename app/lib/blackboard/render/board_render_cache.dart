import '../model/board_geometry.dart';
import 'chalk_text.dart';

/// Measured element bounds in normalized board coordinates.
///
/// The renderers write the real measured box of every element they paint (chalk
/// text size, equation size, diagram extents) and the annotation layer reads it
/// so highlights wrap the content exactly. Falls back to
/// `BoardDocument.boundsOf` when an element has not been painted yet.
class BoardBoundsRegistry {
  final Map<String, BoardRect> _bounds = <String, BoardRect>{};
  int _version = 0;

  int get version => _version;

  BoardRect? operator [](String elementId) => _bounds[elementId];

  BoardRect? get(String elementId) => _bounds[elementId];

  Iterable<MapEntry<String, BoardRect>> get entries => _bounds.entries;

  void put(String elementId, BoardRect bounds) {
    if (bounds.isEmpty) return;
    final previous = _bounds[elementId];
    if (previous != null &&
        (previous.x - bounds.x).abs() < 0.0005 &&
        (previous.y - bounds.y).abs() < 0.0005 &&
        (previous.w - bounds.w).abs() < 0.0005 &&
        (previous.h - bounds.h).abs() < 0.0005) {
      return;
    }
    _bounds[elementId] = bounds;
    _version++;
  }

  void retainOnly(Set<String> aliveIds) {
    final removed = _bounds.keys.where((id) => !aliveIds.contains(id)).toList();
    if (removed.isEmpty) return;
    for (final id in removed) {
      _bounds.remove(id);
    }
    _version++;
  }

  void clear() {
    if (_bounds.isEmpty) return;
    _bounds.clear();
    _version++;
  }
}

/// Bounded LRU cache for computed render geometry (paths, sample arrays, axis
/// layouts) keyed by element id + element revision + layout size.
class BoardGeometryCache {
  BoardGeometryCache({this.maxEntries = 192});

  final int maxEntries;
  final Map<String, Object> _entries = <String, Object>{};
  final List<String> _lru = <String>[];

  int get length => _entries.length;

  /// Stable cache key for an element at the current layout.
  static String keyFor(
    String elementId,
    int revision,
    int widthPx,
    int heightPx, {
    String suffix = '',
  }) =>
      '$elementId|$revision|$widthPx|$heightPx|$suffix';

  T? get<T>(String key) {
    final value = _entries[key];
    if (value == null) return null;
    _lru.remove(key);
    _lru.add(key);
    return value as T;
  }

  void put(String key, Object value) {
    if (_entries.containsKey(key)) {
      _lru.remove(key);
    }
    _entries[key] = value;
    _lru.add(key);
    while (_entries.length > maxEntries) {
      final oldest = _lru.removeAt(0);
      _entries.remove(oldest);
    }
  }

  void clear() {
    _entries.clear();
    _lru.clear();
  }
}

/// Shared per-board render state: measurement caches, geometry caches and
/// measured-bounds registry used by all six layers.
class BoardRenderCache {
  BoardRenderCache({ChalkTextEngine? textEngine})
      : textEngine = textEngine ?? ChalkTextEngine();

  final ChalkTextEngine textEngine;
  final BoardBoundsRegistry bounds = BoardBoundsRegistry();
  final BoardGeometryCache geometry = BoardGeometryCache();

  void prune(Set<String> aliveElementIds) => bounds.retainOnly(aliveElementIds);

  void dispose() {
    textEngine.dispose();
    geometry.clear();
    bounds.clear();
  }
}
