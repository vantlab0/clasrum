import 'dart:ui' show Path, Picture;

import 'package:flutter/foundation.dart' show VoidCallback;

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
  final Map<String, String> _ownerByChild = <String, String>{};
  int _version = 0;
  int _lastPruneRevision = -1;

  int get version => _version;

  /// Optional invalidation hook. Renderers write measurements during paint, so
  /// consumers must schedule their own post-frame work instead of calling
  /// `setState` directly from this callback.
  VoidCallback? onChanged;

  BoardRect? operator [](String elementId) => _bounds[elementId];

  BoardRect? get(String elementId) => _bounds[elementId];

  Iterable<MapEntry<String, BoardRect>> get entries => _bounds.entries;

  /// Ownership used by [putOwned], copied with cached layer measurements so a
  /// reused picture cannot make generated child bounds look like orphans.
  Map<String, String> get owners => Map<String, String>.from(_ownerByChild);

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
    onChanged?.call();
  }

  /// Reapplies measurements captured when a cached layer picture was recorded.
  /// Only missing entries are added, so reusing a static layer never triggers
  /// a notification storm on every animation frame.
  void restore(
    Map<String, BoardRect> cachedBounds,
    Map<String, String> cachedOwners,
  ) {
    var changed = false;
    for (final entry in cachedOwners.entries) {
      if (!_ownerByChild.containsKey(entry.key)) {
        _ownerByChild[entry.key] = entry.value;
        changed = true;
      }
    }
    for (final entry in cachedBounds.entries) {
      if (!_bounds.containsKey(entry.key)) {
        _bounds[entry.key] = entry.value;
        changed = true;
      }
    }
    if (!changed) return;
    _version++;
    onChanged?.call();
  }

  /// Registers geometry belonging to [ownerId] but keyed by a renderer-local
  /// child id (e.g. a generated plan element or a drawing step). The child
  /// survives pruning while its owner element is still in the document.
  void putOwned(String ownerId, String childId, BoardRect bounds) {
    _ownerByChild[childId] = ownerId;
    put(childId, bounds);
  }

  void retainOnly(Set<String> aliveIds) {
    final removed = _bounds.keys
        .where(
          (id) =>
              !aliveIds.contains(id) &&
              !aliveIds.contains(_ownerByChild[id]),
        )
        .toList();
    final deadOwners = _ownerByChild.entries
        .where((entry) => !aliveIds.contains(entry.value))
        .map((entry) => entry.key)
        .toList();
    if (removed.isEmpty && deadOwners.isEmpty) return;
    for (final id in removed) {
      _bounds.remove(id);
    }
    for (final id in deadOwners) {
      _ownerByChild.remove(id);
    }
    _version++;
    onChanged?.call();
  }

  /// Avoids rebuilding the alive-id set on every animation frame when the
  /// document revision has not changed.
  void pruneIfChanged(int revision, Set<String> aliveIds) {
    if (_lastPruneRevision == revision) return;
    _lastPruneRevision = revision;
    retainOnly(aliveIds);
  }

  void clear() {
    if (_bounds.isEmpty && _ownerByChild.isEmpty) return;
    _bounds.clear();
    _ownerByChild.clear();
    _lastPruneRevision = -1;
    _version++;
    onChanged?.call();
  }
}

/// One rasterizable layer snapshot.
///
/// The picture contains paint commands only; bounds are stored separately so a
/// cached static layer can still feed viewport/fullness/annotation consumers
/// without repainting its elements.
class BoardLayerPictureEntry {
  const BoardLayerPictureEntry({
    required this.picture,
    required this.key,
    required this.bounds,
    required this.owners,
  });

  final Picture picture;
  final String key;
  final Map<String, BoardRect> bounds;
  final Map<String, String> owners;
}

/// Per-layer [Picture] reuse for static board content.
///
/// Entries are keyed by document/transform/layout state. A layer with an active
/// animation is invalidated and painted directly; when it settles it is
/// recorded once and replayed with `canvas.drawPicture` until an input changes.
class BoardLayerPictureCache {
  final Map<int, BoardLayerPictureEntry> _entries =
      <int, BoardLayerPictureEntry>{};

  int get length => _entries.length;

  Picture? pictureFor(int layer, String key) {
    final entry = _entries[layer];
    if (entry == null || entry.key != key) return null;
    return entry.picture;
  }

  Map<String, BoardRect>? boundsFor(int layer, String key) {
    final entry = _entries[layer];
    if (entry == null || entry.key != key) return null;
    return entry.bounds;
  }

  Map<String, String>? ownersFor(int layer, String key) {
    final entry = _entries[layer];
    if (entry == null || entry.key != key) return null;
    return entry.owners;
  }

  bool matches(int layer, String key) => _entries[layer]?.key == key;

  void put(
    int layer,
    String key,
    Picture picture,
    Map<String, BoardRect> bounds,
    Map<String, String> owners,
  ) {
    final previous = _entries.remove(layer);
    previous?.picture.dispose();
    _entries[layer] = BoardLayerPictureEntry(
      picture: picture,
      key: key,
      bounds: Map<String, BoardRect>.from(bounds),
      owners: Map<String, String>.from(owners),
    );
  }

  void invalidate(int layer) {
    final previous = _entries.remove(layer);
    previous?.picture.dispose();
  }

  void clear() {
    for (final entry in _entries.values) {
      entry.picture.dispose();
    }
    _entries.clear();
  }

  void dispose() => clear();
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
      _evict(_lru.removeAt(0));
    }
  }

  void clear() {
    for (final key in _entries.keys.toList(growable: false)) {
      _evict(key);
    }
  }

  void dispose() => clear();

  void _evict(String key) {
    final value = _entries.remove(key);
    if (value is Path) {
      value.reset();
    } else if (value is Picture) {
      value.dispose();
    }
  }
}

/// Small bounded LRU for parsed LaTeX syntax trees. Expressions are immutable
/// and frequently parsed again during repaint; clearing the entire map when it
/// fills caused a reparse storm in long lessons.
class LatexExpressionCache {
  LatexExpressionCache({this.maxEntries = 192});

  final int maxEntries;
  final Map<String, Object?> _entries = <String, Object?>{};
  final List<String> _lru = <String>[];

  int get length => _entries.length;

  bool containsKey(String expression) => _entries.containsKey(expression);

  T? get<T extends Object>(String expression) {
    final value = _entries[expression];
    if (value == null) return null;
    _lru.remove(expression);
    _lru.add(expression);
    return value as T;
  }

  /// Cached `null` means a known-invalid expression. [containsKey] distinguishes
  /// it from a miss.
  void put(String expression, Object? syntaxTree) {
    if (_entries.containsKey(expression)) {
      _lru.remove(expression);
    } else {
      while (_entries.length >= maxEntries) {
        _entries.remove(_lru.removeAt(0));
      }
    }
    _entries[expression] = syntaxTree;
    _lru.add(expression);
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
  final LatexExpressionCache latex = LatexExpressionCache();
  final BoardLayerPictureCache layerPictures = BoardLayerPictureCache();

  void prune(Set<String> aliveElementIds) => bounds.retainOnly(aliveElementIds);

  /// Drops stale visual state when a snapshot replaces the document.
  void resetForSnapshot() {
    layerPictures.clear();
    bounds.clear();
  }

  void dispose() {
    textEngine.dispose();
    geometry.dispose();
    latex.clear();
    layerPictures.dispose();
    bounds.clear();
  }
}
