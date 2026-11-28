import 'package:flutter_test/flutter_test.dart';
import 'package:pwony/blackboard/blackboard.dart';

class _FakeFrameDriver implements BoardFrameDriver {
  _FakeFrameDriver(this._onTick);

  final void Function(Duration elapsed) _onTick;
  bool _isTicking = false;

  @override
  bool get isTicking => _isTicking;

  @override
  void start() => _isTicking = true;

  @override
  void stop() => _isTicking = false;

  @override
  void dispose() => _isTicking = false;

  void emit(Duration elapsed) => _onTick(elapsed);
}

BoardEvent _event(int sequence, {String id = 'e'}) {
  return BoardEvent.fromJson(<String, dynamic>{
    'event_id': 'scheduler_$sequence',
    'session_id': 'sess_scheduler',
    'sequence': sequence,
    'type': 'write_text',
    'element_id': id,
    'payload': <String, dynamic>{
      'text': 'text $sequence',
      'style': 'body',
    },
  });
}

void main() {
  test('superseded reveal resolves its completion callback', () {
    late _FakeFrameDriver driver;
    final scheduler = BoardAnimationScheduler(
      driverFactory: (onTick) => driver = _FakeFrameDriver(onTick),
    );
    addTearDown(scheduler.dispose);
    var oldCompleted = 0;
    var newCompleted = 0;

    scheduler.reveal(
      event: _event(1),
      elementId: 'e',
      level: 2,
      duration: const Duration(milliseconds: 400),
      onComplete: () => oldCompleted++,
    );
    scheduler.reveal(
      event: _event(2),
      elementId: 'e',
      level: 2,
      duration: const Duration(milliseconds: 400),
      onComplete: () => newCompleted++,
    );

    expect(oldCompleted, 1);
    expect(newCompleted, 0);
    expect(scheduler.isAnimating, isTrue);
  });

  test('cancelling all animations resolves callbacks and idle waiters', () async {
    late _FakeFrameDriver driver;
    final scheduler = BoardAnimationScheduler(
      driverFactory: (onTick) => driver = _FakeFrameDriver(onTick),
    );
    addTearDown(scheduler.dispose);
    var completed = 0;

    scheduler.reveal(
      event: _event(1),
      elementId: 'e',
      level: 2,
      duration: const Duration(milliseconds: 400),
      onComplete: () => completed++,
    );
    final idle = scheduler.waitForIdle();
    scheduler.cancelAll();

    expect(completed, 1);
    expect(scheduler.isIdle, isTrue);
    await idle.timeout(const Duration(seconds: 1));
  });
}
