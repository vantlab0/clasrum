import 'dart:collection';

import 'board_element.dart';
import 'board_event.dart';
import 'board_geometry.dart';
import 'board_metrics.dart';

/// Immutable board document (CONTRACTS §5) with the pure event reducer.
///
/// Rules implemented here (CONTRACTS §2/§3/§6):
/// * events are applied in `sequence` order and are idempotent
///   (`sequence <= lastSequence` or duplicate `event_id` are ignored);
/// * deletion is soft (`removed_at_sequence`) so replay is stable;
/// * unknown event types and unknown targets are no-ops with a warning — they
///   never throw and never lose stream position;
/// * every event advances `lastSequence`/`revision` exactly once;
/// * `elements` preserves insertion order, which is z-order within a level;
/// * paint order is level first (`1` bottom … `6` top) then insertion order.
class BoardDocument {
  BoardDocument({
    required this.sessionId,
    required this.revision,
    required this.lastSequence,
    Map<String, BoardElement>? elements,
    Set<String>? appliedEventIds,
    this.createdAt,
    this.updatedAt,
    List<String> warnings = const <String>[],
  })  : _elements = Map<String, BoardElement>.unmodifiable(
          elements ?? const <String, BoardElement>{},
        ),
        _appliedEventIds = Set<String>.unmodifiable(
          appliedEventIds ?? const <String>{},
        ),
        _warnings = List<String>.unmodifiable(warnings);

  factory BoardDocument.empty({String sessionId = ''}) => BoardDocument(
        sessionId: sessionId,
        revision: 0,
        lastSequence: 0,
      );

  /// Parses a §5 `BoardDocument` snapshot. Tolerant: wrong shapes produce an
  /// empty document rather than an exception.
  factory BoardDocument.fromJson(Map<String, dynamic> json) {
    final sessionId = (json['session_id'] ?? json['sessionId'] ?? '').toString();
    final revision = asDouble(json['revision'])?.round() ?? 0;
    final lastSequence =
        asDouble(json['last_sequence'] ?? json['lastSequence'])?.round() ?? 0;
    final elements = <String, BoardElement>{};
    final rawElements = json['elements'];
    if (rawElements is List) {
      for (final raw in rawElements) {
        if (raw is! Map) continue;
        final element = BoardElement.fromJson(
          raw.map<String, dynamic>(
            (dynamic key, dynamic value) =>
                MapEntry<String, dynamic>(key.toString(), value),
          ),
        );
        if (element.elementId.isNotEmpty) {
          elements.putIfAbsent(element.elementId, () => element);
        }
      }
    } else if (rawElements is Map) {
      for (final entry in rawElements.entries) {
        final raw = entry.value;
        if (raw is! Map) continue;
        final element = BoardElement.fromJson(
          raw.map<String, dynamic>(
            (dynamic key, dynamic value) =>
                MapEntry<String, dynamic>(key.toString(), value),
          ),
        );
        if (element.elementId.isNotEmpty) {
          elements.putIfAbsent(element.elementId, () => element);
        }
      }
    }
    final applied = <String>{};
    final rawApplied = json['applied_event_ids'];
    if (rawApplied is List) {
      for (final id in rawApplied) {
        if (id != null) applied.add(id.toString());
      }
    }
    return BoardDocument(
      sessionId: sessionId,
      revision: revision,
      lastSequence: lastSequence,
      elements: elements,
      appliedEventIds: applied,
      createdAt: _parseDate(json['created_at']),
      updatedAt: _parseDate(json['updated_at']),
    );
  }

  /// Parses a `BoardSnapshotEnvelope` (CONTRACTS §5): the snapshot document plus
  /// any buffered `board_events` replayed on top of it.
  factory BoardDocument.fromSnapshotEnvelope(Map<String, dynamic> envelope) {
    final rawSnapshot = envelope['snapshot'];
    var document = BoardDocument.empty(
      sessionId: (envelope['session_id'] ?? '').toString(),
    );
    if (rawSnapshot is Map) {
      document = BoardDocument.fromJson(
        rawSnapshot.map<String, dynamic>(
          (dynamic key, dynamic value) =>
              MapEntry<String, dynamic>(key.toString(), value),
        ),
      );
    }
    final events = <BoardEvent>[];
    final rawEvents =
        envelope['board_events'] ?? envelope['events'] ?? envelope['event'];
    if (rawEvents is List) {
      for (final raw in rawEvents) {
        if (raw is Map) {
          events.add(
            BoardEvent.fromJson(
              raw.map<String, dynamic>(
                (dynamic key, dynamic value) =>
                    MapEntry<String, dynamic>(key.toString(), value),
              ),
            ),
          );
        }
      }
    } else if (rawEvents is Map) {
      events.add(
        BoardEvent.fromJson(
          rawEvents.map<String, dynamic>(
            (dynamic key, dynamic value) =>
                MapEntry<String, dynamic>(key.toString(), value),
          ),
        ),
      );
    }
    return document.replay(events);
  }

  final String sessionId;
  final int revision;
  final int lastSequence;

  /// Insertion-ordered element map (insertion = z-order within a level).
  final Map<String, BoardElement> _elements;
  final Set<String> _appliedEventIds;
  final List<String> _warnings;
  final DateTime? createdAt;
  final DateTime? updatedAt;

  Map<String, BoardElement> get elements => _elements;

  Set<String> get appliedEventIds => _appliedEventIds;

  /// Most recent non-fatal reducer warnings (bounded, newest last).
  List<String> get warnings => _warnings;

  bool get isEmpty => _elements.isEmpty;

  bool get hasVisibleElements => _elements.values.any((e) => e.isVisible);

  BoardElement? operator [](String elementId) => _elements[elementId];

  Iterable<BoardElement> get visibleElements =>
      _elements.values.where((e) => e.isVisible);

  /// Paint order: level ascending, then insertion order within the level.
  List<BoardElement> get elementsInPaintOrder {
    final entries = _elements.entries.toList(growable: false);
    final visible = <MapEntry<int, BoardElement>>[];
    var index = 0;
    for (final entry in entries) {
      if (entry.value.isVisible) {
        visible.add(MapEntry<int, BoardElement>(index, entry.value));
      }
      index++;
    }
    visible.sort((a, b) {
      final byLevel = a.value.level.compareTo(b.value.level);
      if (byLevel != 0) return byLevel;
      return a.key.compareTo(b.key);
    });
    return visible.map((entry) => entry.value).toList(growable: false);
  }

  /// Elements for one render layer in paint order (insertion order).
  List<BoardElement> elementsForLayer(int layer) {
    final result = <BoardElement>[];
    for (final element in _elements.values) {
      if (element.isVisible && element.level == layer) result.add(element);
    }
    return result;
  }

  BoardElement? elementById(String? id) =>
      id == null ? null : _elements[id];

  /// Union bounds of the given element ids, for highlight annotations.
  ///
  /// Uses declared bounds when present, otherwise deterministic estimation
  /// (see [BoardBoundsEstimator]); annotations resolve their targets
  /// recursively. Returns null when no target exists or all targets are erased.
  BoardRect? boundsOf(Iterable<String> elementIds) {
    BoardRect? union;
    for (final id in elementIds) {
      final bounds = _boundsOfId(id, 0);
      if (bounds == null) continue;
      union = union == null ? bounds : union.union(bounds);
    }
    return union?.clamp01();
  }

  BoardRect? _boundsOfId(String id, int depth) {
    final element = _elements[id];
    if (element == null || !element.isVisible) return null;
    final declared = element.declaredBounds;
    if (declared != null && !declared.isEmpty) return declared.clamp01();
    final estimated = BoardBoundsEstimator.estimate(element);
    if (estimated != null) return estimated;
    if (depth < 2 &&
        (element.kind == BoardEventType.highlight ||
            element.kind == BoardEventType.highlightEquation)) {
      final targets = <String>[];
      final rawTargets = element.payload['target_ids'];
      if (rawTargets is List) {
        for (final target in rawTargets) {
          if (target != null) targets.add(target.toString());
        }
      }
      final single = element.payload['target_id'];
      if (single != null) targets.add(single.toString());
      if (targets.isNotEmpty) {
        return boundsOf(targets);
      }
    }
    return null;
  }

  /// Pure reducer: returns this document when the event is stale/duplicate,
  /// otherwise a new document with the event applied (or safely ignored).
  BoardDocument apply(BoardEvent event) {
    if (!event.isWellFormed) return _withWarning('ignored malformed event');
    if (event.sequence <= lastSequence) return this;
    if (_appliedEventIds.contains(event.eventId)) return this;
    if (sessionId.isNotEmpty &&
        event.sessionId.isNotEmpty &&
        event.sessionId != sessionId) {
      return _withWarning(
        'ignored event ${event.eventId} for foreign session '
        '${event.sessionId}',
      );
    }

    final builder = _DocumentBuilder(this);
    switch (event.kind) {
      case BoardEventType.writeText:
      case BoardEventType.writeHeading:
      case BoardEventType.writeDefinition:
      case BoardEventType.writeBulletList:
      case BoardEventType.writeEquation:
      case BoardEventType.drawLine:
      case BoardEventType.drawShape:
      case BoardEventType.drawPath:
      case BoardEventType.drawArrow:
      case BoardEventType.drawDiagram:
      case BoardEventType.createCoordinateSystem:
        _applyCreate(builder, event);
        break;
      case BoardEventType.updateText:
        _applyUpdateText(builder, event);
        break;
      case BoardEventType.updateEquation:
        _applyUpdateEquation(builder, event);
        break;
      case BoardEventType.plotFunction:
      case BoardEventType.plotPoints:
        _applyPlot(builder, event);
        break;
      case BoardEventType.addGraphLabel:
        _applyCreate(builder, event);
        break;
      case BoardEventType.highlight:
        _applyHighlight(builder, event);
        break;
      case BoardEventType.highlightEquation:
        _applyHighlightEquation(builder, event);
        break;
      case BoardEventType.erase:
        _applyErase(builder, event);
        break;
      case BoardEventType.eraseElement:
        _applyEraseElement(builder, event);
        break;
      case BoardEventType.clear:
        _applyClear(builder, event);
        break;
      case BoardEventType.moveElement:
        _applyMove(builder, event);
        break;
      case BoardEventType.unknown:
        builder.warn('ignored unknown event type "${event.type}"');
        break;
    }
    return builder.commit(event);
  }

  /// Applies events in the given order (they are sorted by sequence first so a
  /// shuffled backfill still converges). Stale/duplicate events are ignored.
  BoardDocument applyAll(Iterable<BoardEvent> events) {
    final sorted = events.toList(growable: false)
      ..sort((a, b) => a.sequence.compareTo(b.sequence));
    var document = this;
    for (final event in sorted) {
      document = document.apply(event);
    }
    return document;
  }

  /// Replays [events] on top of this document (snapshot@revision + events).
  BoardDocument replay(Iterable<BoardEvent> events) => applyAll(events);

  /// Rebuilds a document from scratch by replaying [events] (snapshot restore
  /// or full transcript replay).
  BoardDocument restore(Iterable<BoardEvent> events) {
    final list = events.toList(growable: false)
      ..sort((a, b) => a.sequence.compareTo(b.sequence));
    final session = sessionId.isNotEmpty
        ? sessionId
        : (list.isEmpty ? '' : list.first.sessionId);
    return BoardDocument.empty(sessionId: session).applyAll(list);
  }

  Map<String, dynamic> toJson() => <String, dynamic>{
        'session_id': sessionId,
        'revision': revision,
        'last_sequence': lastSequence,
        'elements':
            _elements.values.map((element) => element.toJson()).toList(),
        if (_appliedEventIds.isNotEmpty)
          'applied_event_ids': _appliedEventIds.toList(growable: false),
        if (createdAt != null) 'created_at': createdAt!.toUtc().toIso8601String(),
        if (updatedAt != null) 'updated_at': updatedAt!.toUtc().toIso8601String(),
      };

  BoardDocument withSession(String newSessionId) => BoardDocument(
        sessionId: newSessionId,
        revision: revision,
        lastSequence: lastSequence,
        elements: _elements,
        appliedEventIds: _appliedEventIds,
        createdAt: createdAt,
        updatedAt: updatedAt,
        warnings: _warnings,
      );

  BoardDocument _withWarning(String message) => BoardDocument(
        sessionId: sessionId,
        revision: revision,
        lastSequence: lastSequence,
        elements: _elements,
        appliedEventIds: _appliedEventIds,
        createdAt: createdAt,
        updatedAt: updatedAt,
        warnings: _appendWarning(_warnings, message),
      );

  static List<String> _appendWarning(List<String> current, String warning) {
    final next = <String>[...current, warning];
    if (next.length > 12) next.removeRange(0, next.length - 12);
    return next;
  }

  // ---------------------------------------------------------------------------
  // Reducer operations
  // ---------------------------------------------------------------------------

  static void _applyCreate(_DocumentBuilder builder, BoardEvent event) {
    final id = event.elementId;
    if (id == null || id.trim().isEmpty) {
      builder.warn('${event.type} requires element_id');
      return;
    }
    final created = BoardElement.fromEvent(event, sequence: event.sequence);
    final existing = builder.elements[id];
    if (existing != null) {
      if (!existing.isVisible) {
        builder.warn(
          'ignored ${event.type} for erased element $id',
        );
        return;
      }
      final payload = Map<String, dynamic>.unmodifiable(event.payload);
      builder.elements[id] = existing.copyWith(
        type: event.type,
        level: event.layer == 0 ? existing.level : event.layer,
        payload: payload,
        position: event.position ?? existing.position,
        size: event.size ?? existing.size,
        style: event.style ?? existing.style,
        color: event.color ?? existing.color,
        updatedSequence: event.sequence,
      );
      return;
    }
    if (created == null) {
      builder.warn(
        'ignored ${event.type}: invalid payload for element $id',
      );
      return;
    }
    var element = created;
    if (event.kind == BoardEventType.createCoordinateSystem) {
      element = element.copyWith(
        size: event.size ?? const BoardSize(0.5, 0.5),
      );
    } else if (event.kind == BoardEventType.drawShape &&
        element.size == null) {
      element = element.copyWith(size: const BoardSize(0.2, 0.14));
    }
    builder.elements[id] = element;
  }

  static void _applyUpdateText(_DocumentBuilder builder, BoardEvent event) {
    final id = event.elementId;
    final existing = id == null ? null : builder.elements[id];
    if (existing == null) {
      builder.warn('update_text: unknown target ${id ?? '-'}');
      return;
    }
    if (!existing.isVisible) {
      builder.warn('update_text: target $id is erased');
      return;
    }
    if (!existing.kind.isTextual &&
        existing.kind != BoardEventType.writeHeading &&
        existing.kind != BoardEventType.writeDefinition &&
        existing.kind != BoardEventType.writeBulletList) {
      builder.warn('update_text: target $id is not a text element');
      return;
    }
    final merged = Map<String, dynamic>.of(existing.payload);
    for (final key in const <String>[
      'text',
      'style',
      'color',
      'position',
      'size',
      'align',
      'font_scale',
      'max_width',
    ]) {
      if (event.payload.containsKey(key)) merged[key] = event.payload[key];
    }
    final newPosition =
        BoardPoint.tryParse(merged['position']) ?? existing.position;
    builder.elements[id!] = existing.copyWith(
      payload: Map<String, dynamic>.unmodifiable(merged),
      position: newPosition,
      style: event.payload.containsKey('style')
          ? (event.payload['style']?.toString() ?? existing.style)
          : existing.style,
      color: event.payload.containsKey('color')
          ? (event.payload['color']?.toString() ?? existing.color)
          : existing.color,
      size: BoardSize.tryParse(merged['size']) ?? existing.size,
      updatedSequence: event.sequence,
    );
  }

  static void _applyUpdateEquation(
    _DocumentBuilder builder,
    BoardEvent event,
  ) {
    final id = event.elementId;
    final existing = id == null ? null : builder.elements[id];
    if (existing == null) {
      builder.warn('update_equation: unknown target ${id ?? '-'}');
      return;
    }
    if (!existing.isVisible) {
      builder.warn('update_equation: target $id is erased');
      return;
    }
    final merged = Map<String, dynamic>.of(existing.payload);
    for (final key in const <String>[
      'latex',
      'color',
      'font_scale',
      'caption',
    ]) {
      if (event.payload.containsKey(key)) merged[key] = event.payload[key];
    }
    builder.elements[id!] = existing.copyWith(
      payload: Map<String, dynamic>.unmodifiable(merged),
      color: event.payload.containsKey('color')
          ? event.payload['color']?.toString()
          : existing.color,
      updatedSequence: event.sequence,
    );
  }

  static void _applyPlot(_DocumentBuilder builder, BoardEvent event) {
    final id = event.elementId;
    if (id == null || id.trim().isEmpty) {
      builder.warn('${event.type} requires element_id');
      return;
    }
    final coordinateSystemId = event.coordinateSystemId;
    final cs = coordinateSystemId == null
        ? null
        : builder.elements[coordinateSystemId];
    if (cs == null || !cs.isVisible || !cs.isCoordinateSystem) {
      builder.warn(
        '${event.type}: unknown coordinate system '
        '${coordinateSystemId ?? '-'}',
      );
      return;
    }
    final csPosition = cs.position ?? BoardPoint.zero;
    final csSize = cs.size ?? const BoardSize(0.5, 0.5);
    final existing = builder.elements[id];
    if (existing != null && !existing.isVisible) {
      builder.warn('${event.type}: element $id is erased');
      return;
    }
    final payload = Map<String, dynamic>.unmodifiable(event.payload);
    if (existing != null) {
      builder.elements[id] = existing.copyWith(
        payload: payload,
        position: csPosition,
        size: csSize,
        color: event.color ?? existing.color,
        style: event.style ?? existing.style,
        updatedSequence: event.sequence,
      );
      return;
    }
    builder.elements[id] = BoardElement(
      elementId: id,
      type: event.type,
      level: event.layer,
      payload: payload,
      position: csPosition,
      size: csSize,
      style: event.style,
      color: event.color,
      createdSequence: event.sequence,
    );
    if (!event.payloadParsed.isValid) {
      final warnings = event.payloadParsed.warnings;
      if (warnings.isNotEmpty) builder.warn(warnings.first);
    }
  }

  static void _applyHighlight(_DocumentBuilder builder, BoardEvent event) {
    final payload = event.payloadParsed;
    final targets = payload is HighlightPayload ? payload.targetIds : <String>[];
    if (targets.isEmpty) {
      builder.warn('highlight: target_ids is empty');
      return;
    }
    final bounds = builder.document.boundsOf(targets);
    final id = event.elementId ??
        'hl_${event.eventId.isNotEmpty ? event.eventId : event.sequence}';
    final annotationPayload = Map<String, dynamic>.of(event.payload);
    annotationPayload['target_ids'] = targets;
    if (bounds == null) {
      builder.warn('highlight: no live targets among ${targets.join(',')}');
      return;
    }
    builder.elements[id] = BoardElement(
      elementId: id,
      type: event.type,
      level: 6,
      payload: Map<String, dynamic>.unmodifiable(annotationPayload),
      position: BoardPoint(bounds.x, bounds.y),
      size: BoardSize(bounds.w, bounds.h),
      style: event.style,
      color: event.color,
      createdSequence: event.sequence,
    );
  }

  static void _applyHighlightEquation(
    _DocumentBuilder builder,
    BoardEvent event,
  ) {
    final target = event.elementId;
    final existing = target == null ? null : builder.elements[target];
    if (existing == null) {
      builder.warn('highlight_equation: unknown target ${target ?? '-'}');
      return;
    }
    if (!existing.isVisible) {
      builder.warn('highlight_equation: target $target is erased');
      return;
    }
    if (!existing.isEquation) {
      builder.warn('highlight_equation: target $target is not an equation');
      return;
    }
    final bounds = builder.document.boundsOf(<String>[target!]);
    if (bounds == null) {
      builder.warn('highlight_equation: no bounds for $target');
      return;
    }
    final payload = event.payloadParsed;
    final style = payload is HighlightEquationPayload
        ? (payload.style ?? 'box')
        : 'box';
    final id = 'hl_${event.eventId.isNotEmpty ? event.eventId : event.sequence}';
    builder.elements[id] = BoardElement(
      elementId: id,
      type: BoardEventType.highlight.wireName,
      level: 6,
      payload: Map<String, dynamic>.unmodifiable(<String, dynamic>{
        'target_ids': <String>[target],
        'style': style,
        if (event.color != null) 'color': event.color,
        'source_event_id': event.eventId,
      }),
      position: BoardPoint(bounds.x, bounds.y),
      size: BoardSize(bounds.w, bounds.h),
      style: style,
      color: event.color,
      createdSequence: event.sequence,
    );
  }

  static void _applyErase(_DocumentBuilder builder, BoardEvent event) {
    final payload = event.payloadParsed;
    if (payload is! ErasePayload) {
      builder.warn('erase: malformed payload');
      return;
    }
    if (payload.scope == 'region') {
      final region = payload.region;
      if (region == null) {
        builder.warn('erase: region required for scope=region');
        return;
      }
      var removed = 0;
      for (final element in builder.elements.values.toList(growable: false)) {
        if (!element.isVisible) continue;
        final bounds = builder.document.boundsOf(<String>[element.elementId]);
        if (bounds == null || !bounds.intersects(region)) continue;
        builder.elements[element.elementId] = element.copyWith(
          removedAtSequence: event.sequence,
        );
        removed++;
      }
      if (removed == 0) builder.warn('erase: region hit no elements');
      return;
    }
    builder.removeAll(event.sequence);
  }

  static void _applyEraseElement(_DocumentBuilder builder, BoardEvent event) {
    final id = event.elementId;
    final existing = id == null ? null : builder.elements[id];
    if (existing == null) {
      builder.warn('erase_element: unknown target ${id ?? '-'}');
      return;
    }
    if (!existing.isVisible) return;
    builder.elements[id!] = existing.copyWith(
      removedAtSequence: event.sequence,
    );
  }

  static void _applyClear(_DocumentBuilder builder, BoardEvent event) {
    builder.removeAll(event.sequence);
  }

  static void _applyMove(_DocumentBuilder builder, BoardEvent event) {
    final id = event.elementId;
    final existing = id == null ? null : builder.elements[id];
    if (existing == null) {
      builder.warn('move_element: unknown target ${id ?? '-'}');
      return;
    }
    if (!existing.isVisible) {
      builder.warn('move_element: target $id is erased');
      return;
    }
    final payload = event.payloadParsed;
    if (payload is! MoveElementPayload || !payload.isValid) {
      builder.warn('move_element: requires position or delta');
      return;
    }
    final current = existing.position ?? BoardPoint.zero;
    final newPosition = payload.position ?? current + (payload.delta ?? BoardPoint.zero);
    final merged = Map<String, dynamic>.of(existing.payload);
    merged['position'] = newPosition.toJson();
    builder.elements[id!] = existing.copyWith(
      payload: Map<String, dynamic>.unmodifiable(merged),
      position: newPosition.clamp01(),
      updatedSequence: event.sequence,
    );
  }
}

/// Mutable scratch space for one reducer step. Copies the element map lazily so
/// no-op events do not allocate a new map.
class _DocumentBuilder {
  _DocumentBuilder(this.document);

  final BoardDocument document;
  Map<String, BoardElement>? _elements;
  final List<String> _warnings = <String>[];

  Map<String, BoardElement> get elements =>
      _elements ??= LinkedHashMap<String, BoardElement>.of(document.elements);

  void warn(String message) {
    _warnings.add(message);
  }

  void removeAll(int sequence) {
    var any = false;
    for (final entry in elements.entries.toList(growable: false)) {
      if (!entry.value.isVisible) continue;
      elements[entry.key] =
          entry.value.copyWith(removedAtSequence: sequence);
      any = true;
    }
    if (!any) {
      _warnings.add('clear/erase hit an empty board');
    }
  }

  BoardDocument commit(BoardEvent event) {
    var warnings = document.warnings;
    for (final warning in <String>[...event.warnings, ..._warnings]) {
      warnings = BoardDocument._appendWarning(warnings, warning);
    }
    final applied = Set<String>.of(document.appliedEventIds)..add(event.eventId);
    if (applied.length > 2048) {
      applied.removeAll(applied.take(applied.length - 2048).toList());
    }
    final newRevision =
        event.boardRevision > document.revision ? event.boardRevision : document.revision;
    final revision = newRevision < event.sequence ? event.sequence : newRevision;
    return BoardDocument(
      sessionId:
          document.sessionId.isNotEmpty ? document.sessionId : event.sessionId,
      revision: revision,
      lastSequence: event.sequence,
      elements: _elements,
      appliedEventIds: applied,
      createdAt: document.createdAt ?? event.createdAt,
      updatedAt: event.createdAt,
      warnings: warnings,
    );
  }
}

/// Small helper used by BoardDocument.fromJson date parsing.
DateTime? _parseDate(Object? value) {
  if (value == null) return null;
  return DateTime.tryParse(value.toString())?.toUtc();
}
