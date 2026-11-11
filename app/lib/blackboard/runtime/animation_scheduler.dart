import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/scheduler.dart';

import '../model/board_event.dart';
import '../model/board_geometry.dart';

/// Drives the single board clock. Implementations are injectable so the
/// animation scheduler can run deterministically in tests without a binding.
abstract class BoardFrameDriver {
  void start();
  void stop();
  bool get isTicking;
  void dispose();
}

/// Production driver backed by exactly one [Ticker] for the whole board.
class TickerBoardFrameDriver implements BoardFrameDriver {
  TickerBoardFrameDriver(void Function(Duration elapsed) onTick)
      : _ticker = Ticker(onTick, debugLabel: 'BlackboardReveal');

  final Ticker _ticker;

  @override
  void start() => _ticker.start();

  @override
  void stop() => _ticker.stop(canceled: true);

  @override
  bool get isTicking => _ticker.isTicking;

  @override
  void dispose() => _ticker.dispose();
}

typedef BoardFrameDriverFactory = BoardFrameDriver Function(
  void Function(Duration elapsed) onTick,
);

/// A position animation declared by `move_element`.
class BoardMoveAnimation {
  const BoardMoveAnimation({
    required this.from,
    required this.to,
    required this.progress,
  });

  final BoardPoint from;
  final BoardPoint to;
  final double progress;

  BoardPoint resolve(BoardPoint fallback) {
    if (progress >= 1) return to;
    return BoardPoint(
      from.x + (to.x - from.x) * progress,
      from.y + (to.y - from.y) * progress,
    );
  }
}

class _Reveal {
  _Reveal({
    required this.elementId,
    required this.level,
    required this.start,
    required this.duration,
    this.onComplete,
  });

  final String elementId;
  final int level;
  final Duration start;
  final Duration duration;
  final VoidCallback? onComplete;
  double progress = 0;
}

class _MoveReveal {
  _MoveReveal({
    required this.elementId,
    required this.level,
    required this.start,
    required this.duration,
    required this.from,
    required this.to,
    this.onComplete,
  });

  final String elementId;
  final int level;
  final Duration start;
  final Duration duration;
  final BoardPoint from;
  final BoardPoint to;
  final VoidCallback? onComplete;
  double progress = 0;
}

/// Drives per-element chalk reveal progress in `0..1` from a single ticker
/// (CONTRACTS §10: one Ticker/AnimationController for the whole board).
///
/// * per-element, per-type durations (backend `animation.duration_ms` can
///   override via the controller);
/// * reveals are started by the controller in strict event order, so queued
///   operations execute in order and animations never block audio;
/// * `animate: false` events are applied instantly by the controller (no
///   scheduler entry) — instant operations never wait on the clock;
/// * [cancelElement]/[cancelLevel]/[cancelAll] stop reveals immediately
///   (erase/clear semantics) and resolve [waitForIdle].
class BoardAnimationScheduler {
  BoardAnimationScheduler({
    BoardFrameDriverFactory? driverFactory,
    this.animationScale = 1.0,
    this.defaultFrameDuration = const Duration(milliseconds: 16),
  }) : _driverFactory = driverFactory;

  /// Optional multiplier for all reveal durations (`0` disables animation).
  final double animationScale;

  /// Used only by test doubles / callers that manually drive frames.
  final Duration defaultFrameDuration;

  final BoardFrameDriverFactory? _driverFactory;
  final ValueNotifier<int> _frameTick = ValueNotifier<int>(0);
  final Map<String, _Reveal> _reveals = <String, _Reveal>{};
  final Map<String, _MoveReveal> _moves = <String, _MoveReveal>{};
  final Set<int> _activeLevels = <int>{};

  BoardFrameDriver? _driver;
  Duration _clock = Duration.zero;
  Duration _lastDriverElapsed = Duration.zero;
  int _frameEpoch = 0;
  Completer<void>? _idleCompleter;
  bool _disposed = false;

  /// Fired after every animation frame (after [repaint] notifies).
  VoidCallback? onFrame;

  /// Fired when the last reveal completes (the board has settled).
  VoidCallback? onIdle;

  /// Notifier that ticks once per animation frame; painters listen to this.
  Listenable get repaint => _frameTick;

  /// Monotonic frame counter (bumped on every tick) for painter invalidation.
  int get frameEpoch => _frameEpoch;

  bool get isAnimating => _reveals.isNotEmpty || _moves.isNotEmpty;

  bool get isIdle => !isAnimating;

  /// Levels (render layers 1..6) with an active animation right now.
  Set<int> get activeLevels => Set<int>.unmodifiable(_activeLevels);

  bool isAnimatingLevel(int level) => _activeLevels.contains(level);

  /// Reveal progress of [elementId] in `0..1` (1 = fully drawn).
  double progress(String elementId) =>
      _reveals[elementId]?.progress ?? 1.0;

  /// Whether [elementId] is currently being revealed (or moved).
  bool isAnimatingElement(String elementId) =>
      _reveals.containsKey(elementId) || _moves.containsKey(elementId);

  BoardMoveAnimation? moveFor(String elementId) {
    final move = _moves[elementId];
    if (move == null) return null;
    return BoardMoveAnimation(
      from: move.from,
      to: move.to,
      progress: move.progress,
    );
  }

  /// Resolves an element position with any in-flight `move_element` animation.
  BoardPoint resolvedPosition(String elementId, BoardPoint fallback) {
    final move = _moves[elementId];
    if (move == null) return fallback;
    return BoardPoint(
      move.from.x + (move.to.x - move.from.x) * move.progress,
      move.from.y + (move.to.y - move.from.y) * move.progress,
    );
  }

  /// Starts (or restarts) the reveal of [elementId] on [level].
  ///
  /// When [instant] is true the scheduler does not animate: it completes
  /// immediately (used for `animate: false` restorations).
  void reveal({
    required BoardEvent event,
    required String elementId,
    required int level,
    Duration? duration,
    bool instant = false,
    VoidCallback? onComplete,
  }) {
    if (_disposed) {
      onComplete?.call();
      return;
    }
    final effective = _scale(duration ?? defaultDurationFor(event.kind));
    if (instant || effective.inMicroseconds <= 0) {
      _reveals.remove(elementId);
      _moves.remove(elementId);
      _refreshActiveLevels();
      onComplete?.call();
      return;
    }
    _reveals[elementId] = _Reveal(
      elementId: elementId,
      level: level,
      start: _clock,
      duration: effective,
      onComplete: onComplete,
    );
    _moves.remove(elementId);
    _activeLevels.add(level);
    _ensureDriver();
    _notifyFrame();
  }

  /// Animates an element position (`move_element`).
  void move({
    required String elementId,
    required int level,
    required BoardPoint from,
    required BoardPoint to,
    Duration? duration,
    VoidCallback? onComplete,
  }) {
    if (_disposed) {
      onComplete?.call();
      return;
    }
    final effective =
        _scale(duration ?? const Duration(milliseconds: 420));
    if (effective.inMicroseconds <= 0 || from == to) {
      _moves.remove(elementId);
      _refreshActiveLevels();
      onComplete?.call();
      return;
    }
    _moves[elementId] = _MoveReveal(
      elementId: elementId,
      level: level,
      start: _clock,
      duration: effective,
      from: from,
      to: to,
      onComplete: onComplete,
    );
    _activeLevels.add(level);
    _ensureDriver();
    _notifyFrame();
  }

  void cancelElement(String elementId) {
    final removed = _reveals.remove(elementId) != null;
    final moved = _moves.remove(elementId) != null;
    if (removed || moved) {
      _refreshActiveLevels();
      _maybeGoIdle();
    }
  }

  void cancelLevel(int level) {
    _reveals.removeWhere((_, reveal) => reveal.level == level);
    _moves.removeWhere((_, move) => move.level == level);
    _refreshActiveLevels();
    _maybeGoIdle();
  }

  /// Cancels every reveal (clear/erase/snapshot restore) and resolves idle.
  void cancelAll() {
    if (_reveals.isEmpty && _moves.isEmpty) return;
    _reveals.clear();
    _moves.clear();
    _refreshActiveLevels();
    _notifyFrame();
    _maybeGoIdle();
  }

  /// Completes when no animation is running. Completes immediately when idle.
  Future<void> waitForIdle() {
    if (!isAnimating) return Future<void>.value();
    final completer = _idleCompleter ??= Completer<void>();
    return completer.future;
  }

  /// Default reveal duration for an event type; animation metadata on the
  /// element can override this via the controller.
  static Duration defaultDurationFor(BoardEventType type) {
    switch (type) {
      case BoardEventType.writeText:
        return const Duration(milliseconds: 760);
      case BoardEventType.writeHeading:
        return const Duration(milliseconds: 920);
      case BoardEventType.writeDefinition:
        return const Duration(milliseconds: 1050);
      case BoardEventType.writeBulletList:
        return const Duration(milliseconds: 1150);
      case BoardEventType.updateText:
        return const Duration(milliseconds: 430);
      case BoardEventType.writeEquation:
        return const Duration(milliseconds: 1180);
      case BoardEventType.updateEquation:
        return const Duration(milliseconds: 520);
      case BoardEventType.highlightEquation:
        return const Duration(milliseconds: 680);
      case BoardEventType.drawLine:
        return const Duration(milliseconds: 540);
      case BoardEventType.drawShape:
        return const Duration(milliseconds: 700);
      case BoardEventType.drawPath:
        return const Duration(milliseconds: 780);
      case BoardEventType.drawArrow:
        return const Duration(milliseconds: 660);
      case BoardEventType.drawDiagram:
        return const Duration(milliseconds: 1450);
      case BoardEventType.createCoordinateSystem:
        return const Duration(milliseconds: 720);
      case BoardEventType.plotFunction:
        return const Duration(milliseconds: 1500);
      case BoardEventType.plotPoints:
        return const Duration(milliseconds: 920);
      case BoardEventType.addGraphLabel:
        return const Duration(milliseconds: 620);
      case BoardEventType.highlight:
        return const Duration(milliseconds: 640);
      case BoardEventType.moveElement:
        return const Duration(milliseconds: 430);
      case BoardEventType.erase:
      case BoardEventType.eraseElement:
        return const Duration(milliseconds: 240);
      case BoardEventType.clear:
        return const Duration(milliseconds: 300);
      case BoardEventType.unknown:
        return const Duration(milliseconds: 300);
    }
  }

  void dispose() {
    if (_disposed) return;
    _disposed = true;
    _reveals.clear();
    _moves.clear();
    _activeLevels.clear();
    _driver?.dispose();
    _driver = null;
    final completer = _idleCompleter;
    _idleCompleter = null;
    if (completer != null && !completer.isCompleted) completer.complete();
    _frameTick.dispose();
    onFrame = null;
    onIdle = null;
  }

  // ---------------------------------------------------------------------------
  // Clock plumbing
  // ---------------------------------------------------------------------------

  Duration _scale(Duration duration) {
    if (animationScale <= 0) return Duration.zero;
    return Duration(
      microseconds: (duration.inMicroseconds * animationScale).round(),
    );
  }

  void _ensureDriver() {
    if (_driver == null) {
      final factory =
          _driverFactory ?? (onTick) => TickerBoardFrameDriver(onTick);
      _driver = factory(_handleTick);
      _lastDriverElapsed = Duration.zero;
      _driver!.start();
    } else if (!_driver!.isTicking) {
      _lastDriverElapsed = Duration.zero;
      _driver!.start();
    }
  }

  void _handleTick(Duration driverElapsed) {
    if (_disposed) return;
    var delta = driverElapsed - _lastDriverElapsed;
    if (delta.isNegative) delta = Duration.zero;
    _lastDriverElapsed = driverElapsed;
    _clock += delta;
    _advance();
  }

  void _advance() {
    var changed = false;
    final completedReveals = <_Reveal>[];
    for (final reveal in _reveals.values) {
      final progress = _progressFor(_clock - reveal.start, reveal.duration);
      if (progress != reveal.progress) {
        reveal.progress = progress;
        changed = true;
      }
      if (progress >= 1) completedReveals.add(reveal);
    }
    final completedMoves = <_MoveReveal>[];
    for (final move in _moves.values) {
      final progress = _progressFor(_clock - move.start, move.duration);
      if (progress != move.progress) {
        move.progress = progress;
        changed = true;
      }
      if (progress >= 1) completedMoves.add(move);
    }
    for (final reveal in completedReveals) {
      _reveals.remove(reveal.elementId);
      reveal.onComplete?.call();
    }
    for (final move in completedMoves) {
      _moves.remove(move.elementId);
      move.onComplete?.call();
    }
    if (completedReveals.isNotEmpty || completedMoves.isNotEmpty) {
      changed = true;
    }
    if (changed) {
      _refreshActiveLevels();
      _notifyFrame();
    }
    _maybeGoIdle();
  }

  double _progressFor(Duration elapsed, Duration duration) {
    if (duration.inMicroseconds <= 0) return 1;
    final raw = elapsed.inMicroseconds / duration.inMicroseconds;
    return raw.clamp(0.0, 1.0);
  }

  void _refreshActiveLevels() {
    _activeLevels
      ..clear()
      ..addAll(_reveals.values.map((reveal) => reveal.level))
      ..addAll(_moves.values.map((move) => move.level));
  }

  void _notifyFrame() {
    _frameEpoch++;
    _frameTick.value = _frameEpoch;
    onFrame?.call();
  }

  void _maybeGoIdle() {
    if (isAnimating) return;
    _driver?.stop();
    final completer = _idleCompleter;
    _idleCompleter = null;
    if (completer != null && !completer.isCompleted) completer.complete();
    if (onIdle != null) onIdle!();
  }

  @visibleForTesting
  Duration get clock => _clock;

  @visibleForTesting
  void debugAdvanceClock(Duration delta) {
    _clock += delta;
    _advance();
  }
}
