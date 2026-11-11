import 'dart:collection';

import 'package:flutter/foundation.dart';

import '../model/board_event.dart';

/// Called once when a sequence gap is detected: the next expected sequence and
/// the sequence that arrived out of order.
typedef BoardGapCallback = void Function(
  int expectedSequence,
  int receivedSequence,
);

/// Called when the queue needs the transport to backfill: the client should
/// request events with `sequence > afterSequence` (CONTRACTS §6).
typedef BoardBackfillCallback = void Function(int afterSequence);

/// Ordered, idempotent event queue with gap buffering and dependency-aware
/// scheduling (CONTRACTS §2/§6).
///
/// The queue never applies anything by itself: [takeNext] yields the single
/// event at the head of the gap-free sequence and the controller calls
/// [markApplied] after reducing it into the document. Events that arrive ahead
/// of a missing sequence are buffered, and only the contiguous run is emitted
/// once the gap fills. Duplicate `event_id`s and stale/non-increasing
/// sequences are ignored.
///
/// "Animation-aware pacing": the controller pauses the queue while the reveal
/// animation for the previously applied event is running, so board operations
/// are paced in teaching order without ever blocking audio or the UI isolate.
class BoardEventQueue {
  BoardEventQueue({
    this.maxBufferedEvents = 512,
    this.maxRememberedEventIds = 4096,
    this.dependencyGrace = const Duration(seconds: 2),
    DateTime Function()? clock,
  })  : _now = clock ?? DateTime.now,
        _buffered = SplayTreeMap<int, BoardEvent>();

  /// Cap on out-of-order buffered events; oldest-furthest events are dropped
  /// with a warning rather than growing without bound.
  final int maxBufferedEvents;

  /// Cap on remembered event ids used for duplicate suppression.
  final int maxRememberedEventIds;

  /// How long an event may wait on an unsatisfied `depends_on` id before it is
  /// released anyway (missing dependency ids are treated as stale after this).
  final Duration dependencyGrace;

  final DateTime Function() _now;
  final SplayTreeMap<int, BoardEvent> _buffered;
  final LinkedHashSet<String> _rememberedEventIds = LinkedHashSet<String>();
  final LinkedHashSet<String> _appliedEventIds = LinkedHashSet<String>();
  final Map<int, DateTime> _dependencyHeldSince = <int, DateTime>{};
  final List<String> _warnings = <String>[];

  int _appliedSequence = 0;
  bool _paused = false;
  bool _gapActive = false;

  /// Fired once per gap episode when an out-of-order sequence arrives.
  BoardGapCallback? onGapDetected;

  /// Fired once per gap episode so the transport can send `board.backfill`.
  BoardBackfillCallback? onBackfillRequest;

  /// Fired when a previously detected gap has been filled.
  VoidCallback? onGapCleared;

  /// Fired the first time an event is held because `depends_on` is unmet.
  void Function(BoardEvent event)? onDependencyHeld;

  /// Last applied sequence (the document's `lastSequence`).
  int get appliedSequence => _appliedSequence;

  /// The next sequence the queue can emit.
  int get expectedSequence => _appliedSequence + 1;

  bool get hasGap =>
      _buffered.isNotEmpty && !_buffered.containsKey(_appliedSequence + 1);

  bool get hasBufferedEvents => _buffered.isNotEmpty;

  int get pendingCount => _buffered.length;

  List<int> get bufferedSequences =>
      List<int>.unmodifiable(_buffered.keys.toList(growable: false));

  bool get isEmpty => _buffered.isEmpty;

  bool get paused => _paused;

  /// Pause delivery (used while a reveal animation is running).
  set paused(bool value) {
    _paused = value;
  }

  /// Recent non-fatal queue warnings (bounded, newest last).
  List<String> get warnings => List<String>.unmodifiable(_warnings);

  /// Number of events applied through this queue instance.
  int get rememberedEventCount => _rememberedEventIds.length;

  /// Resets the queue to a snapshot position (e.g. after `loadSnapshot`).
  void reset(int appliedSequence) {
    _buffered.clear();
    _rememberedEventIds.clear();
    _appliedEventIds.clear();
    _dependencyHeldSince.clear();
    _warnings.clear();
    _appliedSequence = appliedSequence < 0 ? 0 : appliedSequence;
    _gapActive = false;
    _paused = false;
  }

  /// Enqueues one event. Returns true when it was newly buffered/accepted.
  bool enqueue(BoardEvent event) {
    if (!event.isWellFormed) {
      _warn('ignored malformed event (${event.type} seq=${event.sequence})');
      return false;
    }
    if (event.sequence <= _appliedSequence) {
      // Idempotent replay: already reflected in the document.
      return false;
    }
    if (_appliedEventIds.contains(event.eventId) ||
        _rememberedEventIds.contains(event.eventId)) {
      return false;
    }
    if (_buffered.containsKey(event.sequence)) {
      return false;
    }
    final wasGap = hasGap;
    _buffered[event.sequence] = event;
    _rememberToken(event);
    if (_buffered.length > maxBufferedEvents) {
      final overflow = _buffered.length - maxBufferedEvents;
      final removable = _buffered.keys.toList().reversed.take(overflow).toList();
      for (final sequence in removable) {
        _buffered.remove(sequence);
        _warn('dropped far-future event seq=$sequence (buffer full)');
      }
    }
    final isGapNow = hasGap;
    if (isGapNow && !wasGap) {
      _notifyGap();
    } else if (!isGapNow && _gapActive) {
      _gapActive = false;
      onGapCleared?.call();
    }
    return true;
  }

  void enqueueAll(Iterable<BoardEvent> events) {
    for (final event in events) {
      enqueue(event);
    }
  }

  /// The next contiguous, dependency-satisfied event, or null when paused,
  /// empty, or waiting on a gap. Does not mutate the queue.
  BoardEvent? peekNext() {
    if (_paused) return null;
    final head = _buffered[_appliedSequence + 1];
    if (head == null) return null;
    if (head.dependsOn.isEmpty) return head;
    var satisfied = true;
    for (final dependency in head.dependsOn) {
      if (!_appliedEventIds.contains(dependency)) {
        satisfied = false;
        break;
      }
    }
    if (satisfied) {
      _dependencyHeldSince.remove(head.sequence);
      return head;
    }
    final heldSince = _dependencyHeldSince.putIfAbsent(head.sequence, _now);
    if (_now().difference(heldSince) >= dependencyGrace) {
      _warn(
        'released event seq=${head.sequence} after dependency grace; '
        'unmet depends_on: ${head.dependsOn.join(',')}',
      );
      _dependencyHeldSince.remove(head.sequence);
      return head;
    }
    onDependencyHeld?.call(head);
    return null;
  }

  /// Pops the next contiguous, dependency-satisfied event.
  BoardEvent? takeNext() {
    final event = peekNext();
    if (event == null) return null;
    _buffered.remove(event.sequence);
    return event;
  }

  /// Marks [event] as applied (reduced into the document) and advances the
  /// queue head. Also clears a gap episode when the buffer becomes contiguous.
  void markApplied(BoardEvent event) {
    if (event.sequence > _appliedSequence) {
      _appliedSequence = event.sequence;
    }
    _buffered.remove(event.sequence);
    _appliedEventIds.add(event.eventId);
    _rememberToken(event);
    _dependencyHeldSince.remove(event.sequence);
    if (_gapActive && !hasGap) {
      _gapActive = false;
      onGapCleared?.call();
    }
  }

  /// Marks a sequence as applied without the event object (snapshot restore).
  void markAppliedSequence(int sequence) {
    if (sequence > _appliedSequence) _appliedSequence = sequence;
    _buffered.removeWhere((key, _) => key <= _appliedSequence);
    _dependencyHeldSince.clear();
    if (_gapActive && !hasGap) {
      _gapActive = false;
      onGapCleared?.call();
    }
  }

  /// Clears buffered events (does not change the applied sequence).
  void clearPending() {
    _buffered.clear();
    _dependencyHeldSince.clear();
    if (_gapActive) {
      _gapActive = false;
      onGapCleared?.call();
    }
  }

  void dispose() {
    _buffered.clear();
    _rememberedEventIds.clear();
    _appliedEventIds.clear();
    _dependencyHeldSince.clear();
    onGapDetected = null;
    onBackfillRequest = null;
    onGapCleared = null;
    onDependencyHeld = null;
  }

  void _notifyGap() {
    _gapActive = true;
    final expected = _appliedSequence + 1;
    final received = _buffered.isEmpty ? expected : _buffered.keys.last;
    onGapDetected?.call(expected, received);
    onBackfillRequest?.call(_appliedSequence);
  }

  void _rememberToken(BoardEvent event) {
    _rememberedEventIds.add(event.eventId);
    if (_rememberedEventIds.length > maxRememberedEventIds) {
      final excess = _rememberedEventIds.length - maxRememberedEventIds;
      final ids = _rememberedEventIds.take(excess).toList();
      for (final id in ids) {
        _rememberedEventIds.remove(id);
      }
    }
  }

  void _warn(String message) {
    _warnings.add(message);
    if (_warnings.length > 32) _warnings.removeAt(0);
  }
}
