import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/scheduler.dart';
import 'package:flutter/ui.dart' show Offset, Size;

import '../../lib/blackboard/model/board_document.dart';
import '../../lib/blackboard/model/board_element.dart';
import '../../lib/blackboard/model/board_event.dart';
import '../../lib/blackboard/model/board_geometry.dart';
import '../../lib/blackboard/runtime/board_viewport_controller.dart';

/// Minimal [TickerProvider] for tests without a Flutter binding.
class _FakeTickerProvider extends ChangeNotifier implements TickerProvider {
  @override
  Ticker createTicker(TickerCallback onTick, {String? debugLabel}) {
    return _FakeTicker(onTick, debugLabel: debugLabel);
  }
}

class _FakeTicker extends ChangeNotifier implements Ticker {
  _FakeTicker(this._onTick, {String? debugLabel}) : _debugLabel = debugLabel;
  final TickerCallback _onTick;
  final String? _debugLabel;
  bool _isTicking = false;
  Timer? _timer;

  @override
  bool get isTicking => _isTicking;

  @override
  void start() {
    _isTicking = true;
    _timer = Timer.periodic(const Duration(milliseconds: 16), (_) {
      if (_isTicking) _onTick(const Duration(milliseconds: 16));
    });
  }

  @override
  void stop({bool canceled = false}) {
    _isTicking = false;
    _timer?.cancel();
    _timer = null;
  }

  @override
  void dispose() {
    stop();
    super.dispose();
  }
}

void main() {
  group('BoardViewportController defaults', () {
    test('starts at zero offset and unit scale', () {
      final c = BoardViewportController();
      expect(c.offset, Offset.zero);
      expect(c.scale, 1.0);
      expect(c.isManuallyInteracting, isFalse);
      expect(c.isAnimating, isFalse);
    });

    test('isMobile is false with no viewport size', () {
      final c = BoardViewportController();
      expect(c.isMobile, isFalse);
      expect(c.autoPanEnabled, isFalse);
    });

    test('isMobile is true below breakpoint', () {
      final c = BoardViewportController(mobileBreakpoint: 600);
      c.onViewportSizeChanged(const Size(400, 800));
      expect(c.isMobile, isTrue);
      expect(c.autoPanEnabled, isTrue);
    });

    test('isMobile is false above breakpoint', () {
      final c = BoardViewportController(mobileBreakpoint: 600);
      c.onViewportSizeChanged(const Size(1024, 768));
      expect(c.isMobile, isFalse);
      expect(c.autoPanEnabled, isFalse);
    });

    test('forceAutoPan enables auto-pan on large viewports', () {
      final c = BoardViewportController(
        mobileBreakpoint: 600,
        forceAutoPan: true,
      );
      c.onViewportSizeChanged(const Size(1024, 768));
      expect(c.isMobile, isFalse);
      expect(c.autoPanEnabled, isTrue);
    });
  });

  group('setViewport and clamping', () {
    test('clamps offset within valid range', () {
      final c = BoardViewportController();
      c.onViewportSizeChanged(const Size(400, 800));
      // Pan far right — should clamp.
      c.setViewport(const Offset(5.0, 0.0), 1.0);
      expect(c.offset.dx, greaterThanOrEqualTo(0.0));
      expect(c.offset.dx, lessThanOrEqualTo(1.0));
    });

    test('clamps scale to min/max', () {
      final c = BoardViewportController();
      c.onViewportSizeChanged(const Size(400, 800));
      c.setViewport(Offset.zero, 0.1);
      expect(c.scale, 0.5); // minScale
      c.setViewport(Offset.zero, 10.0);
      expect(c.scale, 2.0); // maxScale
    });

    test('cancels in-flight animation on setViewport', () async {
      final c = BoardViewportController();
      c.attachTickerProvider(_FakeTickerProvider());
      c.onViewportSizeChanged(const Size(400, 800));

      // Start an animation.
      c.focusOnRect(
        const BoardRect(0.8, 0.8, 0.2, 0.2),
        animate: true,
      );
      expect(c.isAnimating, isTrue);

      // Immediately override with setViewport.
      c.setViewport(const Offset(0.1, 0.1), 1.0);
      expect(c.offset, const Offset(0.1, 0.1));
      expect(c.isAnimating, isFalse);
    });
  });

  group('content bounds clamping', () {
    test('clamps to content bounds when set', () {
      final c = BoardViewportController();
      c.onViewportSizeChanged(const Size(400, 400));
      // Content occupies right half of board.
      c.onContentBoundsChanged(
        const BoardRect(0.6, 0.0, 0.4, 1.0),
      );
      // Pan left past content — should clamp.
      c.setViewport(const Offset(-1.0, 0.0), 1.0);
      expect(c.offset.dx, greaterThanOrEqualTo(0.5));
    });
  });

  group('comfortable visibility check', () {
    test('rect already visible returns true', () {
      final c = BoardViewportController();
      c.onViewportSizeChanged(const Size(400, 400));
      // Full board visible at scale 1.
      expect(
        c.isComfortablyVisible(const BoardRect(0.2, 0.2, 0.6, 0.6)),
        isTrue,
      );
    });

    test('rect outside viewport returns false', () {
      final c = BoardViewportController();
      c.onViewportSizeChanged(const Size(400, 400));
      expect(
        c.isComfortablyVisible(const BoardRect(0.8, 0.8, 0.3, 0.3)),
        isFalse,
      );
    });
  });

  group('auto-pan gating', () {
    test('auto-pan disabled on desktop without force', () {
      final c = BoardViewportController(mobileBreakpoint: 600);
      c.onViewportSizeChanged(const Size(1024, 768)); // desktop
      expect(c.autoPanEnabled, isFalse);
      // focusOnRect should be a no-op — offset stays zero.
      c.focusOnRect(const BoardRect(0.9, 0.9, 0.1, 0.1));
      expect(c.offset, Offset.zero);
    });

    test('auto-pan enabled on mobile', () {
      final c = BoardViewportController(mobileBreakpoint: 600);
      c.onViewportSizeChanged(const Size(400, 800)); // mobile
      expect(c.autoPanEnabled, isTrue);
    });
  });

  group('manual interaction cooldown', () {
    test('cooldown suspends auto-pan then resumes', () async {
      final c = BoardViewportController(
        mobileBreakpoint: 600,
        manualCooldown: const Duration(milliseconds: 50),
      );
      c.onViewportSizeChanged(const Size(400, 800));

      // Manually interact.
      c.onManualInteractionStart();
      expect(c.isManuallyInteracting, isTrue);
      c.onManualInteractionEnd();
      expect(c.isManuallyInteracting, isTrue); // still in cooldown

      // Wait for cooldown.
      await Future<void>.delayed(const Duration(milliseconds: 100));
      expect(c.isManuallyInteracting, isFalse);
    });
  });

  group('listener notification', () {
    test('notifies listeners on change', () {
      final c = BoardViewportController();
      var notifyCount = 0;
      c.addListener(() => notifyCount++);
      c.onViewportSizeChanged(const Size(400, 800));
      expect(notifyCount, greaterThan(0));
    });
  });

  group('dispose', () {
    test('disposes without error', () {
      final c = BoardViewportController();
      c.attachTickerProvider(_FakeTickerProvider());
      expect(() => c.dispose(), returnsNormally);
    });
  });
}
