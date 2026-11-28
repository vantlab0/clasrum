import 'dart:ui' show Color;

import 'package:flutter_test/flutter_test.dart';
import 'package:pwony/blackboard/blackboard.dart';

void main() {
  test('animated alpha does not churn the text measurement cache', () {
    final engine = ChalkTextEngine();
    addTearDown(engine.dispose);
    final spec = ChalkTextStyleSpec.forName('body');

    engine.layout(
      text: 'Photosynthesis makes food.',
      spec: spec,
      color: const Color(0xFFFFFFFF),
      fontSizePx: 18,
      maxWidthPx: 240,
    );
    final baseline = engine.cachedLayoutCount;
    expect(baseline, 1);

    for (var i = 1; i < 20; i++) {
      engine.layout(
        text: 'Photosynthesis makes food.',
        spec: spec,
        color: Color.fromRGBO(255, 255, 255, i / 20),
        fontSizePx: 18,
        maxWidthPx: 240,
      );
    }
    expect(engine.cachedLayoutCount, baseline);
  });

  test('different text lengths never share a cache key', () {
    final engine = ChalkTextEngine();
    addTearDown(engine.dispose);
    final spec = ChalkTextStyleSpec.forName('body');
    engine.layout(
      text: 'a',
      spec: spec,
      color: const Color(0xFFFFFFFF),
      fontSizePx: 18,
      maxWidthPx: 240,
    );
    final long = engine.layout(
      text: 'a much longer sentence with a different meaning',
      spec: spec,
      color: const Color(0xFFFFFFFF),
      fontSizePx: 18,
      maxWidthPx: 240,
    );
    expect(long.text, 'a much longer sentence with a different meaning');
    expect(engine.cachedLayoutCount, 2);
  });
}
