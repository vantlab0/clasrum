import 'dart:async';

import 'package:flutter/foundation.dart';

import '../model/board_collision.dart';
import '../model/board_document.dart';
import '../model/board_event.dart';
import '../model/board_geometry.dart';
import '../render/board_render_cache.dart';
import 'animation_scheduler.dart';
import 'event_queue.dart';

/// State owner for the blackboard (CONTRACTS §10).
///
/// Document state is kept separate from animation state: events are reduced into
/// the immutable [BoardDocument] in strict sequence order as soon as they are
/// applied, while the reveal of each element is driven independently by the
/// single [BoardAnimationScheduler] clock. Listeners are notified when the
/// document changes; painters also listen to [repaint] for animation frames.
class BlackboardController extends ChangeNotifier {
  BlackboardController({
    BoardEventQueue? queue,
    BoardFrameDriverFactory? frameDriverFactory,
    double animationScale = 1.0,
    bool animationsEnabled = true,
    BoardDocument? initialDocument,
  })  : _queue = queue ?? BoardEventQueue(),
        _scheduler = BoardAnimationScheduler(
          driverFactory: frameDriverFactory,
          animationScale: animationScale,
        ),
        _animationsEnabled = animationsEnabled,
        _document = initialDocument ?? BoardDocument.empty() {
    _queue.onBackfillRequest = _handleBackfillRequest;
    _queue.onGapDetected = (expected, received) {
      onGapDetected?.call(expected, received);
    };
    _scheduler.onIdle = _handleSchedulerIdle;
    if (initialDocument != null) {
      _queue.reset(initialDocument.lastSequence);
    }
  }

  final BoardEventQueue _queue;
  final BoardAnimationScheduler _scheduler;
  final Set<String> _instantEventIds = <String>{};

  /// Shared measurement/bounds state survives repaints and animation frames.
  final BoardRenderCache renderCache = BoardRenderCache();

  BoardDocument _document;
  BoardEvent? _currentEvent;
  bool _animationsEnabled;
  bool _disposed = false;
  bool _pumping = false;

  /// Set by the transport layer to request `board.backfill` over the WebSocket
  /// (CONTRACTS §6) when a sequence gap is detected.
  BoardBackfillCallback? onBackfillRequested;

  /// Fired once per detected gap; useful for diagnostics/telemetry.
  BoardGapCallback? onGapDetected;

  /// Optional non-fatal warning sink (parse warnings, unknown targets, gaps).
  void Function(String warning)? onWarning;

  final List<String> _warnings = <String>[];

  /// Current immutable board document.
  BoardDocument get document => _document;

  /// Last event sequence reduced into [document].
  int get appliedSequence => _document.lastSequence;

  /// True while a reveal/move animation is in flight.
  bool get isAnimating => _scheduler.isAnimating || _currentEvent != null;

  /// Animation frame notifier for layout layers (one ticker for the whole board).
  Listenable get repaint => _scheduler.repaint;

  /// The reveal clock, read by the layer painters for per-element progress.
  BoardAnimationScheduler get animations => _scheduler;

  /// Ordered event queue (gap buffering, dedupe, backfill detection).
  BoardEventQueue get queue => _queue;

  bool get hasGap => _queue.hasGap;

  bool get hasPendingEvents => _queue.pendingCount > 0;

  int get queuedEventCount => _queue.pendingCount;

  /// Whether newly applied events animate (`false` = instant apply, e.g.
  /// reduced-motion or replay).
  bool get animationsEnabled => _animationsEnabled;

  set animationsEnabled(bool value) {
    if (_animationsEnabled == value) return;
    _animationsEnabled = value;
    if (!value) {
      _scheduler.cancelAll();
      // cancelAll drops completion callbacks, so a mid-reveal event would
      // otherwise leave _currentEvent set and stall the queue forever.
      _currentEvent = null;
      _pump();
    }
  }

  /// Recent non-fatal warnings (bounded, newest last).
  List<String> get warnings => List<String>.unmodifiable(_warnings);

  /// Pauses the reveal clock without losing animation state (used while the
  /// app is backgrounded). Animations never block audio.
  void pauseAnimations() {
    if (_disposed) return;
    _scheduler.pause();
  }

  /// Resumes a clock frozen by [pauseAnimations] and drains any queued events.
  void resumeAnimations() {
    if (_disposed) return;
    _scheduler.resume();
    _pump();
  }

  /// Replaces the document with a snapshot (e.g. `session.ready` / REST resume).
  void loadSnapshot(BoardDocument? doc) {
    if (_disposed) return;
    final base = doc ?? BoardDocument.empty();
    _scheduler.cancelAll();
    _currentEvent = null;
    _document = base;
    _queue.reset(base.lastSequence);
    _instantEventIds.clear();
    // Snapshot replacement must invalidate the painter even when the sequence
    // is unchanged (e.g. a full restore at the same revision).
    _scheduler.notifyFrame();
    _warnForUnrelatedOverlaps(base);
    notifyListeners();
  }

  /// Applies every event in [documents] order into a fresh document without
  /// animation — convenience for replay/restore screens and tests.
  void loadReplay(List<BoardEvent> events) {
    if (_disposed) return;
    final replay =
        BoardDocument.empty(sessionId: _document.sessionId).applyAll(events);
    loadSnapshot(replay);
  }

  /// Enqueues a live event: ordered, deduplicated, gap-buffered.
  void enqueue(BoardEvent event) {
    if (_disposed || !_acceptsEvent(event)) return;
    final accepted = _queue.enqueue(event);
    if (accepted) _pump();
  }

  /// Enqueues a batch (`board.events` replay frame).
  void enqueueAll(List<BoardEvent> events) {
    if (_disposed) return;
    for (final event in events) {
      if (!_acceptsEvent(event)) continue;
      _queue.enqueue(event);
    }
    _pump();
  }

  /// A foreign-session event is a reducer no-op. Filtering it before the queue
  /// keeps `queue.appliedSequence` in lockstep with the document: otherwise a
  /// valid event sharing its sequence would be rejected forever.
  bool _acceptsEvent(BoardEvent event) {
    if (event.sequence <= appliedSequence) return false;
    return _document.sessionId.isEmpty ||
        event.sessionId.isEmpty ||
        event.sessionId == _document.sessionId;
  }

  /// Applies reconnect backfill events instantly (no reveal animation), as they
  /// are catch-up history rather than live teaching beats (CONTRACTS §6).
  void backfill(List<BoardEvent> events) {
    if (_disposed) return;
    if (_currentEvent != null) {
      // Backfill supersedes every live teaching beat, not just the current one.
      _scheduler.cancelAll();
      _currentEvent = null;
    }
    final sorted = events.toList(growable: false)
      ..sort((a, b) => a.sequence.compareTo(b.sequence));
    for (final event in sorted) {
      if (!_acceptsEvent(event)) continue;
      _instantEventIds.add(event.eventId);
      _queue.enqueue(event);
    }
    _pump();
  }

  /// Completes when every buffered event has been reduced and all reveals are
  /// finished. An unresolved sequence gap is treated as drained: the transport
  /// has already been asked to backfill it via [onBackfillRequested].
  Future<void> waitForIdle() async {
    while (!_disposed) {
      _pump();
      if (!isAnimating) {
        if (!hasPendingEvents || hasGap || _queue.paused || _scheduler.isPaused) {
          return;
        }
        // Dependency holds are released by the queue's grace timer; yield so
        // that timer can make progress without spinning the caller's isolate.
        await Future<void>.delayed(const Duration(milliseconds: 16));
        continue;
      }
      await _scheduler.waitForIdle();
    }
  }

  /// Test/debug helper: advances the animation clock without a real ticker.
  @visibleForTesting
  void debugTick(Duration delta) {
    _scheduler.debugAdvanceClock(delta);
  }

  void _handleBackfillRequest(int afterSequence) {
    _warn('sequence gap after $afterSequence; requested backfill');
    onBackfillRequested?.call(afterSequence);
  }

  void _handleSchedulerIdle() {
    if (_disposed) return;
    _pump();
  }

  /// Core pump: applies contiguous queued events one teaching beat at a time.
  ///
  /// Instant operations (animate:false, backfill, erase/clear, no visual
  /// element) are drained synchronously; the first animated event starts its
  /// reveal and the pump pauses until [BoardAnimationScheduler] completes it.
  void _pump() {
    if (_pumping || _disposed) return;
    _pumping = true;
    var documentChanged = false;
    try {
      while (_currentEvent == null) {
        final event = _queue.peekNext();
        if (event == null) break;
        final before = _document;
        final elementId = event.elementId;
        final beforeElement = elementId == null ? null : before[elementId];
        _queue.markApplied(event);
        final updated = before.apply(event);
        final advanced = updated.lastSequence != before.lastSequence;
        _document = updated;
        if (!advanced) continue;
        documentChanged = true;
        _collectEventWarnings(event);
        _currentEvent = event;
        // Consume the instant marker only when the event is actually applied.
        // A backfilled event blocked behind a gap must keep its marker until
        // the missing sequence arrives.
        final instant = _instantEventIds.remove(event.eventId);
        final kind = event.kind;

        if (kind == BoardEventType.clear) {
          _scheduler.cancelAll();
          _currentEvent = null;
          continue;
        }
        if (kind == BoardEventType.erase) {
          _cancelAnimationsRemovedBy(updated, event.sequence);
          _currentEvent = null;
          continue;
        }
        if (kind == BoardEventType.eraseElement) {
          final erased = event.elementId;
          if (erased != null) _scheduler.cancelElement(erased);
          _currentEvent = null;
          continue;
        }

        final targetElementId = _elementIdFor(event, updated);
        final element =
            targetElementId == null ? null : updated[targetElementId];
        final shouldAnimate = _animationsEnabled &&
            event.animate &&
            !instant &&
            element != null &&
            element.isVisible &&
            event.layer > 0;
        if (!shouldAnimate) {
          _currentEvent = null;
          continue;
        }

        final duration = element.requestedDurationMs == null
            ? null
            : Duration(milliseconds: element.requestedDurationMs!);
        if (kind == BoardEventType.moveElement) {
          final from = beforeElement?.position;
          final to = element.position;
          if (from == null || to == null || from == to) {
            _currentEvent = null;
            continue;
          }
          _scheduler.move(
            elementId: element.elementId,
            level: element.level,
            from: from,
            to: to,
            duration: duration,
            onComplete: () => _handleRevealCompleted(event),
          );
        } else {
          _scheduler.reveal(
            event: event,
            elementId: element.elementId,
            level: element.level,
            duration: duration,
            onComplete: () => _handleRevealCompleted(event),
          );
        }
        // A zero-duration reveal completes synchronously inside the call above;
        // the nested pump is suppressed by _pumping, so continue here instead
        // of leaving the loop waiting for a ticker that will never start.
        if (_currentEvent != null) break;
      }
    } finally {
      _pumping = false;
      if (documentChanged && !_disposed) {
        _scheduler.notifyFrame();
        notifyListeners();
      }
    }
  }

  void _handleRevealCompleted([BoardEvent? completed]) {
    if (_disposed) return;
    if (completed != null && !identical(completed, _currentEvent)) return;
    _currentEvent = null;
    _pump();
  }

  void _cancelAnimationsRemovedBy(BoardDocument updated, int sequence) {
    for (final element in updated.elements.values) {
      if (element.removedAtSequence == sequence) {
        _scheduler.cancelElement(element.elementId);
      }
    }
  }

  /// Resolves the element id created/updated by [event] in [document].
  String? _elementIdFor(BoardEvent event, BoardDocument document) {
    // Annotation events carry an explicit *target* id but create a separate
    // annotation element. Prefer the element created by this sequence so the
    // reveal animates the visible annotation, not the already-painted target.
    for (final element in document.elements.values) {
      if (element.createdSequence == event.sequence) return element.elementId;
    }
    final explicit = event.elementId;
    if (explicit != null && document[explicit] != null) return explicit;
    return explicit;
  }

  void _collectEventWarnings(BoardEvent event) {
    for (final warning in event.warnings) {
      _warn(warning);
    }
    final documentWarnings = _document.warnings;
    if (documentWarnings.isNotEmpty) {
      final latest = documentWarnings.last;
      if (!_warnings.contains(latest)) _warn(latest);
    }
    final queueWarnings = _queue.warnings;
    if (queueWarnings.isNotEmpty) {
      final latest = queueWarnings.last;
      if (!_warnings.contains(latest)) _warn(latest);
    }
  }

  /// Snapshot restore bypasses the reducer, so collision validation is done
  /// explicitly here. Grouped/parented overlaps are intentionally ignored.
  void _warnForUnrelatedOverlaps(BoardDocument document) {
    final overlaps = BoardCollisionDetector.unrelated(document.elements.values);
    if (overlaps.isEmpty) return;
    final sample = overlaps.take(2).map(
          (collision) =>
              '${collision.first.elementId}/${collision.second.elementId}',
        );
    _warn(
      'restored board has ${overlaps.length} unrelated overlap(s): '
      '${sample.join(', ')}',
    );
  }

  void _warn(String message) {
    _warnings.add(message);
    if (_warnings.length > 32) _warnings.removeAt(0);
    onWarning?.call(message);
  }

  @override
  void dispose() {
    if (_disposed) return;
    _disposed = true;
    _scheduler.cancelAll();
    _scheduler.dispose();
    _queue.dispose();
    renderCache.dispose();
    _document = BoardDocument.empty();
    super.dispose();
  }
}

/// Convenience getter for annotation element bounds used by tests/tools.
extension BoardDocumentAnnotationBounds on BoardDocument {
  BoardRect? annotationBounds(String elementId) {
    final element = this[elementId];
    if (element == null) return null;
    final targets = <String>[];
    final raw = element.payload['target_ids'];
    if (raw is List) {
      for (final target in raw) {
        if (target != null) targets.add(target.toString());
      }
    }
    return boundsOf(targets);
  }
}

/// Type re-export used by consumers that configure the queue's backfill hook.
typedef BlackboardBackfillCallback = void Function(int afterSequence);
