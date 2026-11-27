import 'package:flutter_test/flutter_test.dart';
import 'package:pwony/blackboard/blackboard.dart';

class _FakeFrameDriver implements BoardFrameDriver {
  _FakeFrameDriver(this._onTick);

  final void Function(Duration elapsed) _onTick;
  bool _isTicking = false;
  int startCount = 0;
  int stopCount = 0;

  @override
  bool get isTicking => _isTicking;

  @override
  void start() {
    startCount++;
    _isTicking = true;
  }

  @override
  void stop() {
    stopCount++;
    _isTicking = false;
  }

  @override
  void dispose() {
    _isTicking = false;
  }

  void emit(Duration elapsed) => _onTick(elapsed);
}

BoardEvent _event(
  int sequence, {
  String id = 'e',
  String sessionId = 'sess_runtime',
  bool animate = true,
  Map<String, dynamic> extra = const <String, dynamic>{},
}) {
  return BoardEvent.fromJson(<String, dynamic>{
    'event_id': 'runtime_${sessionId}_$sequence',
    'session_id': sessionId,
    'sequence': sequence,
    'type': 'write_text',
    'element_id': id,
    'payload': <String, dynamic>{
      'text': 'lesson text $sequence',
      'style': 'body',
      'position': <String, dynamic>{'x': 0.08, 'y': 0.1 + sequence * 0.08},
      'size': <String, dynamic>{'w': 0.3, 'h': 0.05},
      ...extra,
    },
    'created_at': '2026-11-27T00:00:00.000Z',
    'board_revision': sequence,
    'animate': animate,
  });
}

void main() {
  test('instant document changes bump the repaint epoch', () {
    final controller = BlackboardController(animationsEnabled: false);
    addTearDown(controller.dispose);
    final before = controller.animations.frameEpoch;

    controller.enqueue(_event(1, animate: true));
    expect(controller.document.lastSequence, 1);
    expect(controller.animations.frameEpoch, greaterThan(before));

    final afterApply = controller.animations.frameEpoch;
    controller.loadSnapshot(controller.document);
    expect(controller.animations.frameEpoch, greaterThan(afterApply));
  });

  test('backfilled event behind a gap stays instant', () {
    _FakeFrameDriver? driver;
    final controller = BlackboardController(
      frameDriverFactory: (onTick) {
        driver = _FakeFrameDriver(onTick);
        return driver!;
      },
    );
    addTearDown(controller.dispose);

    // A backfilled seq 2 arrives first and is buffered; it must keep its
    // instant marker until the missing seq 1 arrives.
    controller.backfill(<BoardEvent>[_event(2)]);
    expect(controller.document.lastSequence, 0);
    controller.backfill(<BoardEvent>[_event(1)]);

    expect(controller.document.lastSequence, 2);
    expect(driver, isNull);
    expect(controller.isAnimating, isFalse);
  });

  test('disabling animations mid-reveal drains the queue', () {
    _FakeFrameDriver? driver;
    final controller = BlackboardController(
      frameDriverFactory: (onTick) {
        driver = _FakeFrameDriver(onTick);
        return driver!;
      },
    );
    addTearDown(controller.dispose);

    controller.enqueue(_event(1));
    expect(driver!.startCount, greaterThan(0));
    controller.enqueue(_event(2));
    expect(controller.document.lastSequence, 1);

    controller.animationsEnabled = false;
    expect(controller.document.lastSequence, 2);
    expect(controller.isAnimating, isFalse);
  });

  test('zero-duration reveals do not stall the pump', () {
    _FakeFrameDriver? driver;
    final controller = BlackboardController(
      animationScale: 0,
      frameDriverFactory: (onTick) {
        driver = _FakeFrameDriver(onTick);
        return driver!;
      },
    );
    addTearDown(controller.dispose);

    controller.enqueueAll(<BoardEvent>[_event(1), _event(2)]);
    expect(controller.document.lastSequence, 2);
    expect(driver, isNull);
    expect(controller.isAnimating, isFalse);
  });

  test('pause freezes clock progress and resume continues from that point', () {
    _FakeFrameDriver? driver;
    final controller = BlackboardController(
      frameDriverFactory: (onTick) {
        driver = _FakeFrameDriver(onTick);
        return driver!;
      },
    );
    addTearDown(controller.dispose);

    controller.enqueue(_event(1));
    driver!.emit(const Duration(milliseconds: 100));
    final frozen = controller.animations.progress('e');
    expect(frozen, greaterThan(0));
    expect(frozen, lessThan(1));

    controller.pauseAnimations();
    driver!.emit(const Duration(milliseconds: 1000));
    expect(controller.animations.progress('e'), frozen);

    controller.resumeAnimations();
    driver!.emit(const Duration(milliseconds: 100));
    expect(controller.animations.progress('e'), greaterThan(frozen));
  });

  test('foreign-session events never desynchronize the queue', () {
    final controller = BlackboardController(
      initialDocument: BoardDocument.empty(sessionId: 's1'),
      animationsEnabled: false,
    );
    addTearDown(controller.dispose);

    controller.enqueue(_event(1, id: 'foreign', sessionId: 's2'));
    controller.enqueue(_event(1, id: 'valid', sessionId: 's1'));

    expect(controller.document['foreign'], isNull);
    expect(controller.document['valid'], isNotNull);
    expect(controller.document.lastSequence, 1);
    expect(controller.hasPendingEvents, isFalse);
  });
}
