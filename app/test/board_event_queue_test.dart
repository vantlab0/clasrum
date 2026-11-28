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

  test('gap behind a contiguous prefix requests backfill again after advancing', () {
    final queue = BoardEventQueue();
    addTearDown(queue.dispose);
    queue.markAppliedSequence(5);
    final backfills = <int>[];
    queue.onBackfillRequest = backfills.add;

    queue.enqueue(_event(6));
    queue.enqueue(_event(8)); // gap behind the contiguous prefix: 7 is missing
    expect(backfills, <int>[6]);

    queue.markApplied(queue.takeNext()!); // applied 6; 7 is still missing
    expect(backfills, <int>[6]);

    // Inserting 7 exposes the next hole (9), so the transport is asked again.
    queue.enqueue(_event(7));
    queue.enqueue(_event(10));
    expect(backfills, <int>[6, 8]);

    final seventh = queue.takeNext()!;
    expect(seventh.sequence, 7);
    queue.markApplied(seventh);
    expect(queue.takeNext()!.sequence, 8);
  });

  test('applied dependencies stay satisfied after the grace window', () {
    var now = DateTime(2026, 1, 1);
    final queue = BoardEventQueue(
      clock: () => now,
      dependencyGrace: const Duration(seconds: 2),
    );
    addTearDown(queue.dispose);
    final dependency = _event(1);
    queue.markApplied(dependency);

    now = now.add(const Duration(seconds: 30));
    queue.enqueue(_event(2, dependsOn: <String>[dependency.eventId]));

    expect(queue.peekNext()!.sequence, 2);
    expect(queue.warnings, isEmpty);
  });

  test('overflow-dropped far-future events can still be backfilled', () {
    final queue = BoardEventQueue(maxBufferedEvents: 2);
    addTearDown(queue.dispose);
    queue.enqueue(_event(2));
    queue.enqueue(_event(3));
    queue.enqueue(_event(4)); // dropped from the bounded buffer
    expect(queue.bufferedSequences, <int>[2, 3]);

    // Filling the gap drops the furthest buffered event (3); draining the
    // prefix leaves space for the originally dropped event (4) to return.
    queue.enqueue(_event(1));
    expect(queue.bufferedSequences, <int>[1, 2]);
    for (final expected in <int>[1, 2]) {
      final event = queue.takeNext()!;
      expect(event.sequence, expected);
      queue.markApplied(event);
    }

    expect(queue.enqueue(_event(4)), isTrue);
    expect(queue.bufferedSequences, <int>[4]);
    queue.enqueue(_event(3));
    final third = queue.takeNext()!;
    expect(third.sequence, 3);
    queue.markApplied(third);
    expect(queue.takeNext()!.sequence, 4);
  });
}
