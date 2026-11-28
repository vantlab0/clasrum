import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:pwony/blackboard/blackboard.dart';
import 'package:pwony/blackboard/render/board_render_cache.dart'
    show BoardBoundsRegistry, BoardLayerPictureCache;
import 'package:pwony/blackboard/runtime/board_viewport_controller.dart'
    show BoardViewportController;

BoardEvent _textEvent({
  required int sequence,
  required String id,
  required double y,
}) {
  return BoardEvent.fromJson(<String, dynamic>{
    'event_id': 'layer_cache_$sequence',
    'session_id': 'sess_layer_cache',
    'sequence': sequence,
    'type': 'write_text',
    'element_id': id,
    'payload': <String, dynamic>{
      'text': 'Lesson line $sequence',
      'style': 'body',
      'position': <String, dynamic>{'x': 0.06, 'y': y},
      'size': <String, dynamic>{'w': 0.4, 'h': 0.05},
    },
    'created_at': '2026-11-28T00:00:00.000Z',
    'board_revision': sequence,
    'animate': false,
  });
}

ui.Picture _emptyPicture() {
  final recorder = ui.PictureRecorder();
  ui.Canvas(recorder);
  return recorder.endRecording();
}

void main() {
  group('BoardLayerPictureCache', () {
    test('keyed reuse, replacement and clear dispose pictures safely', () {
      final cache = BoardLayerPictureCache();
      cache.put(2, 'key-a', _emptyPicture(), const <String, BoardRect>{},
          const <String, String>{});
      expect(cache.matches(2, 'key-a'), isTrue);
      expect(cache.pictureFor(2, 'key-a'), isNotNull);
      expect(cache.pictureFor(2, 'key-b'), isNull);

      cache.put(2, 'key-b', _emptyPicture(), const <String, BoardRect>{},
          const <String, String>{});
      expect(cache.matches(2, 'key-a'), isFalse);
      expect(cache.matches(2, 'key-b'), isTrue);

      cache.invalidate(2);
      expect(cache.length, 0);
      cache.put(3, 'key-c', _emptyPicture(), const <String, BoardRect>{},
          const <String, String>{});
      cache.dispose();
      expect(cache.length, 0);
    });
  });

  group('BoardBoundsRegistry.restore', () {
    test('adds only missing measurements without duplicate notifications', () {
      final registry = BoardBoundsRegistry();
      var notifications = 0;
      registry.onChanged = () => notifications++;

      registry.put('a', const BoardRect(0.1, 0.1, 0.2, 0.02));
      registry.putOwned('p', 'child', const BoardRect(0.2, 0.2, 0.1, 0.02));
      expect(notifications, 2);

      registry.restore(
        <String, BoardRect>{
          'a': const BoardRect(0.1, 0.1, 0.2, 0.02),
          'offscreen': const BoardRect(0.1, 2.5, 0.2, 0.02),
        },
        <String, String>{'child': 'p'},
      );
      expect(notifications, 3);
      expect(registry['offscreen'], isNotNull);

      // Replaying the same snapshot on a later frame must be silent.
      registry.restore(
        <String, BoardRect>{
          'a': const BoardRect(0.1, 0.1, 0.2, 0.02),
          'offscreen': const BoardRect(0.1, 2.5, 0.2, 0.02),
        },
        <String, String>{'child': 'p'},
      );
      expect(notifications, 3);
    });
  });

  testWidgets('offscreen elements are culled but keep conservative bounds',
      (tester) async {
    final controller = BlackboardController(animationsEnabled: false);
    addTearDown(controller.dispose);
    controller.enqueueAll(<BoardEvent>[
      _textEvent(sequence: 1, id: 'near_1', y: 0.05),
      _textEvent(sequence: 2, id: 'near_2', y: 0.20),
      _textEvent(sequence: 3, id: 'far', y: 2.50),
    ]);
    await controller.waitForIdle();

    final viewport = BoardViewportController();
    addTearDown(viewport.dispose);
    viewport.onContentBoundsChanged(const BoardRect(0, 0, 1, 2.82));
    viewport.setViewport(ui.Offset.zero, 1.0, userInitiated: true);

    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: SizedBox(
          width: 800,
          height: 600,
          child: BlackboardView(
            controller: controller,
            viewportController: viewport,
          ),
        ),
      ),
    ));
    await tester.pump();

    // The far text is outside the visible rect; it must not be painted, but a
    // conservative measurement still exists for extent/follow/fullness.
    expect(BlackboardView.debugPaintedElementCount, lessThanOrEqualTo(2));
    expect(controller.renderCache.bounds['far'], isNotNull);
    expect(tester.takeException(), isNull);
  });

  testWidgets('static layer pictures are reused until invalidated',
      (tester) async {
    final controller = BlackboardController(animationsEnabled: false);
    addTearDown(controller.dispose);
    controller.enqueueAll(<BoardEvent>[
      _textEvent(sequence: 1, id: 'line_1', y: 0.05),
      _textEvent(sequence: 2, id: 'line_2', y: 0.20),
    ]);
    await controller.waitForIdle();

    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: SizedBox(
          width: 800,
          height: 600,
          child: BlackboardView(controller: controller),
        ),
      ),
    ));
    await tester.pump();

    final firstFrame = BlackboardView.debugPaintedElementCount;
    expect(firstFrame, 2);
    expect(controller.renderCache.layerPictures.length, greaterThan(0));

    // A quiescent pump must not traverse or repaint any element again (the
    // cached layer pictures remain valid and the painter need not run).
    BlackboardView.debugPaintedElementCount = 0;
    await tester.pump();
    expect(BlackboardView.debugPaintedElementCount, 0);
    expect(controller.renderCache.layerPictures.length, greaterThan(0));

    // Restoring a snapshot drops pictures and bounds, forcing one fresh pass.
    controller.loadSnapshot(controller.document);
    await tester.pump();
    expect(BlackboardView.debugPaintedElementCount, 2);
    expect(controller.renderCache.layerPictures.length, greaterThan(0));
    expect(tester.takeException(), isNull);
  });
}
