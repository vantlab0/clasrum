import 'package:flutter_test/flutter_test.dart';
import 'package:pwony/blackboard/blackboard.dart';
import 'package:pwony/blackboard/render/board_render_cache.dart'
    show BoardBoundsRegistry, LatexExpressionCache;

void main() {
  test('renderer child bounds survive pruning while their owner is alive', () {
    final registry = BoardBoundsRegistry();
    registry.putOwned('plan_1', 'body', const BoardRect(0.1, 0.2, 0.3, 0.04));
    registry.putOwned('plan_1', 'label', const BoardRect(0.1, 0.25, 0.2, 0.03));
    registry.putOwned('plan_2', 'tail', const BoardRect(0.8, 0.8, 0.1, 0.02));

    registry.retainOnly(<String>{'plan_1'});
    expect(registry['body'], isNotNull);
    expect(registry['label'], isNotNull);
    expect(registry['tail'], isNull);

    registry.retainOnly(<String>{});
    expect(registry['body'], isNull);
    expect(registry['label'], isNull);
  });

  test('pruneIfChanged skips repeated frames at the same document revision', () {
    final registry = BoardBoundsRegistry();
    registry.put('old', const BoardRect(0, 0, 0.1, 0.1));
    final version = registry.version;
    registry.pruneIfChanged(7, <String>{'new'});
    expect(registry['old'], isNull);
    registry.put('new', const BoardRect(0.2, 0.2, 0.1, 0.1));
    final afterFirst = registry.version;
    registry.pruneIfChanged(7, <String>{'new'});
    expect(registry.version, afterFirst);
    expect(registry['new'], isNotNull);
    expect(version, lessThan(afterFirst));
  });

  test('latex cache uses LRU eviction instead of clearing everything', () {
    final cache = LatexExpressionCache(maxEntries: 2);
    cache.put('a', 'tree-a');
    cache.put('b', 'tree-b');
    cache.get('a');
    cache.put('c', 'tree-c');
    expect(cache.containsKey('a'), isTrue);
    expect(cache.containsKey('b'), isFalse);
    expect(cache.containsKey('c'), isTrue);
    expect(cache.get<String>('a'), 'tree-a');
  });
}
