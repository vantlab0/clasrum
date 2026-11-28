import 'package:flutter_test/flutter_test.dart';
import 'package:pwony/blackboard/blackboard.dart';

BoardEvent _event(
  int sequence, {
  String id = 'e',
  List<String> dependsOn = const <String>[],
}) {
  return BoardEvent.fromJson(<String, dynamic>{
    'event_id': 'queue_${id}_$sequence',
    'session_id': 'sess_queue',
    'sequence': sequence,
    'type': 'write_text',
    'element_id': id,
    'payload': <String, dynamic>{
      'text': 'text $sequence',
      'style': 'body',
    },
    'depends_on': dependsOn,
  });
}

void main() {
  test('dependency-held callback fires once per held event', () {
    final queue = BoardEventQueue(
      dependencyGrace: const Duration(seconds: 5),
    );
    addTearDown(queue.dispose);
    queue.markAppliedSequence(0);
    var held = 0;
    queue.onDependencyHeld = (_) => held++;

    queue.enqueue(_event(1, dependsOn: const <String>{'missing-id'}.toList()));
    expect(queue.peekNext(), isNull);
    expect(queue.peekNext(), isNull);
    expect(queue.peekNext(), isNull);
    expect(held, 1);
  });

  test('queued events retain contiguous order and dedupe replays', () {
    final queue = BoardEventQueue();
    addTearDown(queue.dispose);
    queue.markAppliedSequence(4);
    queue.enqueue(_event(6));
    queue.enqueue(_event(5));
    expect(queue.hasGap, isFalse);
    expect(queue.takeNext()!.sequence, 5);
    queue.markApplied(_event(5));
    expect(queue.takeNext()!.sequence, 6);
    queue.markApplied(_event(6));
    expect(queue.enqueue(_event(6)), isFalse);
    expect(queue.appliedSequence, 6);
  });
}
