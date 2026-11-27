import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:pwony/blackboard/blackboard.dart';
import 'package:pwony/blackboard/model/board_transform.dart';

BoardEvent _event({
  required int sequence,
  required String id,
  required String type,
  required Map<String, dynamic> payload,
  String eventId = '',
  bool animate = false,
}) {
  return BoardEvent.fromJson(<String, dynamic>{
    'event_id': eventId.isNotEmpty ? eventId : 'dup_$sequence',
    'session_id': 'sess_dup',
    'sequence': sequence,
    'type': type,
    'element_id': id,
    'payload': payload,
    'created_at': '2026-11-27T00:00:00.000Z',
    'board_revision': sequence,
    'animate': animate,
  });
}

Map<String, dynamic> _textPayload(
  String text,
  String style, {
  double x = 0.08,
  double y = 0.08,
  int level = 0,
  double fontScale = 1,
}) =>
    <String, dynamic>{
      'text': text,
      'style': style,
      'heading_level': level,
      'font_scale': fontScale,
      'position': <String, dynamic>{'x': x, 'y': y},
      'max_width': 0.95,
    };

Map<String, dynamic> _diagramPlan() => <String, dynamic>{
      'protocol': 'clasrum.drawing',
      'version': 1,
      'title': 'Labelled cycle',
      'elements': <dynamic>[
        <String, dynamic>{
          'id': 'body',
          'kind': 'ellipse',
          'geometry': <String, dynamic>{
            'center': <String, dynamic>{'x': 0.5, 'y': 0.5},
            'rx': 0.18,
            'ry': 0.12,
          },
          'layer': 0,
          'stage': 1,
          'duration_ms': 300,
        },
        <String, dynamic>{
          'id': 'label',
          'kind': 'text',
          'geometry': <String, dynamic>{
            'text': 'Sunlight',
            'position': <String, dynamic>{'x': 0.5, 'y': 0.56},
          },
          'layer': 2,
          'stage': 2,
          'duration_ms': 200,
        },
      ],
      'stages': <dynamic>[
        <String, dynamic>{
          'stage': 1,
          'description': 'body',
          'element_ids': <String>['body'],
        },
        <String, dynamic>{
          'stage': 2,
          'description': 'label',
          'element_ids': <String>['label'],
        },
      ],
    };

void main() {
  group('logical event dedupe', () {
    test('fingerprint ignores volatile replay metadata', () {
      final first = _event(
        sequence: 1,
        id: 'line_1',
        type: 'write_text',
        payload: _textPayload('Force equals mass times acceleration.', 'body'),
      );
      final replay = _event(
        sequence: 9,
        eventId: 'replay_9',
        id: 'line_1',
        type: 'write_text',
        payload: _textPayload('Force equals mass times acceleration.', 'body'),
      );
      expect(
        boardEventFingerprint(first.kind.wireName, first.elementId, first.payload),
        boardEventFingerprint(
            replay.kind.wireName, replay.elementId, replay.payload),
      );

      final document = BoardDocument.empty(sessionId: 'sess_dup')
          .apply(first)
          .apply(replay);
      expect(document.elements.length, 1);
      expect(document.lastSequence, 9,
          reason: 'a deduped replay still advances the stream position');
    });

    test('repeated highlight_equation creates one stable annotation', () {
      final target = _event(
        sequence: 1,
        id: 'eq_1',
        type: 'write_equation',
        payload: <String, dynamic>{
          'latex': r'F = ma',
          'text': r'F = ma',
          'position': <String, dynamic>{'x': 0.1, 'y': 0.2},
        },
      );
      Map<String, dynamic> highlight(int sequence, String eventId) =>
          <String, dynamic>{
            'event_id': eventId,
            'session_id': 'sess_dup',
            'sequence': sequence,
            'type': 'highlight_equation',
            'element_id': 'eq_1',
            'payload': <String, dynamic>{
              'target_id': 'eq_1',
              'style': 'box',
              'color': 'yellow',
            },
            'created_at': '2026-11-27T00:00:00.000Z',
            'board_revision': sequence,
            'animate': false,
          };

      final document = BoardDocument.empty(sessionId: 'sess_dup').applyAll([
        target,
        BoardEvent.fromJson(highlight(2, 'hl_a')),
        BoardEvent.fromJson(highlight(3, 'hl_b')),
      ]);
      final annotations = document.elements.values
          .where((element) => element.kind == BoardEventType.highlight)
          .toList();
      expect(annotations, hasLength(1));
      expect(annotations.single.elementId, startsWith('hle_'));
      expect(document.lastSequence, 3);
    });
  });

  group('single render path', () {
    test('drawDiagram chooses exactly one encoding', () {
      expect(
        boardDiagramUsesLegacySteps(<String, dynamic>{
          'steps': <dynamic>[
            <String, dynamic>{'op': 'line'}
          ],
          'nodes': <dynamic>[],
        }),
        isTrue,
      );
      expect(
        boardDiagramUsesLegacySteps(<String, dynamic>{
          'nodes': <dynamic>[
            <String, dynamic>{'id': 'a'}
          ],
          'edges': <dynamic>[],
        }),
        isFalse,
      );
    });

    testWidgets('same-event diagram edges resolve to node bounds', (tester) async {
      final document = BoardDocument.empty(sessionId: 'sess_dup').apply(
        _event(
          sequence: 1,
          id: 'diagram_1',
          type: 'draw_diagram',
          payload: <String, dynamic>{
            'nodes': <dynamic>[
              <String, dynamic>{
                'id': 'node_a',
                'label': 'A',
                'shape': 'circle',
                'position': <String, dynamic>{'x': 0.2, 'y': 0.3},
                'size': <String, dynamic>{'w': 0.12, 'h': 0.08},
              },
              <String, dynamic>{
                'id': 'node_b',
                'label': 'B',
                'shape': 'circle',
                'position': <String, dynamic>{'x': 0.6, 'y': 0.3},
                'size': <String, dynamic>{'w': 0.12, 'h': 0.08},
              },
            ],
            'edges': <dynamic>[
              <String, dynamic>{'from': 'node_a', 'to': 'node_b'},
            ],
          },
        ),
      );
      final controller = BlackboardController(
        animationsEnabled: false,
        initialDocument: document,
      );
      addTearDown(controller.dispose);
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

      expect(controller.renderCache.bounds['node_a'], isNotNull);
      expect(controller.renderCache.bounds['node_b'], isNotNull);
      expect(controller.renderCache.bounds['diagram_1'], isNotNull);
      expect(tester.takeException(), isNull);
    });
  });

  group('typography and progressive reveal', () {
    test('chalk measurements preserve heading order, wrapping and font scale', () {
      final engine = ChalkTextEngine();
      addTearDown(engine.dispose);
      final transform = BoardTransform.fromSize(const Size(1000, 800));
      final heights = <double>[];
      for (var level = 1; level <= 6; level++) {
        final spec = ChalkTextStyleSpec.forName('heading', headingLevel: level);
        heights.add(engine.measure(
          text: 'Photosynthesis',
          spec: spec,
          color: Colors.white,
          fontSizePx: transform.fontPixels(spec.heightFraction),
          maxWidthPx: 700,
        ).height);
      }
      for (var i = 1; i < heights.length; i++) {
        expect(heights[i], lessThan(heights[i - 1]));
      }

      final body = ChalkTextStyleSpec.forName('body');
      final single = engine.measure(
        text: 'Short line',
        spec: body,
        color: Colors.white,
        fontSizePx: transform.fontPixels(body.heightFraction),
        maxWidthPx: 900,
      );
      final wrapped = engine.measure(
        text: 'Photosynthesis converts light into chemical energy ' * 4,
        spec: body,
        color: Colors.white,
        fontSizePx: transform.fontPixels(body.heightFraction),
        maxWidthPx: 220,
      );
      expect(wrapped.height, greaterThan(single.height * 3));
      expect(wrapped.width, lessThanOrEqualTo(221));

      final enlarged = engine.measure(
        text: 'Short line',
        spec: body,
        color: Colors.white,
        fontSizePx: transform.fontPixels(body.heightFraction) * 2,
        maxWidthPx: 900,
      );
      expect(enlarged.height, greaterThan(single.height * 1.8));
    });

    test('progressive reveal uncovers wrapped lines in reading order', () {
      final engine = ChalkTextEngine();
      addTearDown(engine.dispose);
      final spec = ChalkTextStyleSpec.forName('body');
      final layout = engine.layout(
        text: 'First line\nSecond line\nThird line',
        spec: spec,
        color: Colors.white,
        fontSizePx: 24,
        maxWidthPx: 260,
      );
      final early = layout.revealClip(0.2).getBounds();
      final complete = layout.revealClip(1).getBounds();
      expect(early.isEmpty, isFalse);
      expect(early.bottom, lessThan(complete.bottom));
      expect(layout.revealEdge(0.2).dx, lessThan(layout.revealEdge(1).dx));
    });

    test('same-length chalk lines get different grain seeds', () {
      final engine = ChalkTextEngine();
      addTearDown(engine.dispose);
      final spec = ChalkTextStyleSpec.forName('body');
      final first = engine.layout(
        text: 'AAAAAAAAAAAA',
        spec: spec,
        color: Colors.white,
        fontSizePx: 24,
        maxWidthPx: 400,
      );
      final second = engine.layout(
        text: 'BBBBBBBBBBBB',
        spec: spec,
        color: Colors.white,
        fontSizePx: 24,
        maxWidthPx: 400,
      );
      expect(engine.cachedLayoutCount, 2);
      expect(first.grain.map((s) => s.dx).toList(),
          isNot(second.grain.map((s) => s.dx).toList()));
    });

    test('derived annotation receives the reveal, not its target', () {
      late BoardFrameDriver driver;
      final controller = BlackboardController(
        frameDriverFactory: (onTick) {
          driver = _ImmediateFrameDriver(onTick);
          return driver;
        },
      );
      addTearDown(controller.dispose);
      controller.enqueue(_event(
        sequence: 1,
        id: 'eq_1',
        type: 'write_equation',
        payload: <String, dynamic>{
          'latex': r'F = ma',
          'text': r'F = ma',
          'position': <String, dynamic>{'x': 0.1, 'y': 0.2},
        },
      ));
      controller.enqueue(_event(
        sequence: 2,
        id: 'eq_1',
        type: 'highlight_equation',
        payload: <String, dynamic>{
          'target_id': 'eq_1',
          'style': 'box',
          'color': 'yellow',
        },
        animate: true,
      ));
      final annotation = controller.document.elements.values.firstWhere(
        (element) => element.kind == BoardEventType.highlight,
      );
      expect(controller.animations.isAnimatingElement(annotation.elementId), isTrue);
      expect(controller.animations.isAnimatingElement('eq_1'), isFalse);
    });

    testWidgets('measurement cache survives repaints', (tester) async {
      final controller = BlackboardController(animationsEnabled: false)
        ..enqueue(_event(
          sequence: 1,
          id: 'line_1',
          type: 'write_text',
          payload: _textPayload('Persistent chalk cache', 'body'),
        ));
      await controller.waitForIdle();
      addTearDown(controller.dispose);
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
      final cached = controller.renderCache.textEngine.cachedLayoutCount;
      expect(cached, greaterThan(0));
      await tester.pump();
      expect(controller.renderCache.textEngine.cachedLayoutCount, cached);
    });
  });

  group('mobile and consecutive layout', () {
    testWidgets('consecutive headings and body render without overlap',
        (tester) async {
      final document = BoardDocument.empty(sessionId: 'sess_dup').applyAll([
        _event(
            sequence: 1,
            id: 'h1_a',
            type: 'write_text',
            payload: _textPayload('Cell Structure', 'heading', y: 0.08, level: 1)),
        _event(
            sequence: 2,
            id: 'h1_b',
            type: 'write_text',
            payload: _textPayload('Energy Flow', 'heading', y: 0.22, level: 1)),
        _event(
            sequence: 3,
            id: 'body_1',
            type: 'write_text',
            payload: _textPayload('Mitochondria release ATP.', 'body', y: 0.34)),
      ]);
      final controller = BlackboardController(
        animationsEnabled: false,
        initialDocument: document,
      );
      addTearDown(controller.dispose);
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

      final first = controller.renderCache.bounds['h1_a']!;
      final second = controller.renderCache.bounds['h1_b']!;
      final body = controller.renderCache.bounds['body_1']!;
      expect(second.y, greaterThanOrEqualTo(first.y + first.h));
      expect(body.y, greaterThanOrEqualTo(second.y + second.h));
      expect(tester.takeException(), isNull);
    });

    testWidgets('mobile portrait generative diagram labels stay on board',
        (tester) async {
      final controller = BlackboardController(animationsEnabled: false)
        ..enqueue(_event(
          sequence: 1,
          id: 'visual_1',
          type: 'draw_plan',
          payload: <String, dynamic>{
            ..._diagramPlan(),
            'position': <String, dynamic>{'x': 0.08, 'y': 0.2},
            'size': <String, dynamic>{'w': 0.84, 'h': 0.4},
          },
        ));
      await controller.waitForIdle();
      addTearDown(controller.dispose);
      await tester.pumpWidget(MaterialApp(
        home: Scaffold(
          body: SizedBox(
            width: 390,
            height: 780,
            child: BlackboardView(controller: controller),
          ),
        ),
      ));
      await tester.pump();

      expect(controller.renderCache.bounds['visual_1'], isNotNull);
      expect(tester.takeException(), isNull);
    });
  });
}

class _ImmediateFrameDriver implements BoardFrameDriver {
  _ImmediateFrameDriver(this._onTick);

  final void Function(Duration elapsed) _onTick;
  bool _ticking = false;

  @override
  bool get isTicking => _ticking;

  @override
  void start() => _ticking = true;

  @override
  void stop() => _ticking = false;

  @override
  void dispose() => _ticking = false;

  void emit(Duration elapsed) => _onTick(elapsed);
}
