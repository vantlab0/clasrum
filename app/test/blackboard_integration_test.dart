import 'package:flutter/material.dart';
import 'package:flutter_math_fork/flutter_math.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:pwony/blackboard/blackboard.dart';
import 'package:pwony/blackboard/model/board_transform.dart';

/// Events below mirror the payload shapes emitted by the backend layout
/// manager: coordinates are normalized and already authoritative; Flutter only
/// reduces and renders them.
BoardEvent _event({
  required int sequence,
  required String type,
  required String id,
  required Map<String, dynamic> payload,
  bool animate = false,
}) {
  return BoardEvent.fromJson(<String, dynamic>{
    'event_id': 'integration_$sequence',
    'session_id': 'sess_integration',
    'sequence': sequence,
    'type': type,
    'element_id': id,
    'payload': payload,
    'created_at': '2026-11-27T00:00:00.000Z',
    'board_revision': sequence,
    'animate': animate,
  });
}

Map<String, dynamic> _position(double x, double y) =>
    <String, dynamic>{'x': x, 'y': y};

Map<String, dynamic> _size(double w, double h) =>
    <String, dynamic>{'w': w, 'h': h};

Map<String, dynamic> _visualPlan() => <String, dynamic>{
      'protocol': 'clasrum.drawing',
      'version': 1,
      'title': 'Forces diagram',
      'elements': <dynamic>[
        <String, dynamic>{
          'id': 'body',
          'kind': 'ellipse',
          'geometry': <String, dynamic>{
            'center': _position(0.5, 0.5),
            'rx': 0.12,
            'ry': 0.09,
          },
          'style': <String, dynamic>{'stroke_color': 'blue'},
          'layer': 0,
          'stage': 1,
          'duration_ms': 300,
        },
        <String, dynamic>{
          'id': 'label',
          'kind': 'text',
          'geometry': <String, dynamic>{
            'text': 'F = ma',
            'position': _position(0.5, 0.55),
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

List<BoardEvent> _lessonEvents() => <BoardEvent>[
      _event(
        sequence: 1,
        type: 'write_text',
        id: 'title_h3',
        payload: <String, dynamic>{
          'text': 'Newton’s second law',
          'style': 'subheading',
          'heading_level': 3,
          'position': _position(0.06, 0.06),
          'size': _size(0.5, 0.07),
        },
      ),
      _event(
        sequence: 2,
        type: 'write_text',
        id: 'definition',
        payload: <String, dynamic>{
          'text': 'Force equals mass times acceleration.',
          'style': 'body',
          'position': _position(0.06, 0.17),
          'size': _size(0.52, 0.06),
        },
      ),
      _event(
        sequence: 3,
        type: 'write_text',
        id: 'equation',
        payload: <String, dynamic>{
          'text': r'F = m a',
          'style': 'equation',
          'latex': r'F = m a',
          'position': _position(0.06, 0.28),
          'size': _size(0.24, 0.08),
        },
      ),
      _event(
        sequence: 4,
        type: 'draw_plan',
        id: 'visual',
        payload: <String, dynamic>{
          ..._visualPlan(),
          'position': _position(0.55, 0.18),
          'size': _size(0.38, 0.3),
          'region': 'right',
        },
      ),
      _event(
        sequence: 5,
        type: 'highlight',
        id: 'hl_equation',
        payload: <String, dynamic>{
          'target_ids': <String>['equation'],
          'style': 'box',
          'color': 'yellow',
        },
      ),
    ];

void main() {
  testWidgets('one persistent board renders text, LaTeX, visuals and highlights',
      (tester) async {
    final controller = BlackboardController(animationsEnabled: false);
    addTearDown(controller.dispose);
    controller.enqueueAll(_lessonEvents());
    await controller.waitForIdle();

    expect(controller.document.lastSequence, 5);
    expect(controller.document.elements.keys, containsAll(<String>[
      'title_h3',
      'definition',
      'equation',
      'visual',
      'hl_equation',
    ]));
    expect(controller.document.unrelatedCollisions, isEmpty);

    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: SizedBox(
          width: 1000,
          height: 700,
          child: BlackboardView(controller: controller),
        ),
      ),
    ));
    await tester.pump();

    expect(find.byWidgetPredicate((widget) => widget is Math), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  test('collision validation warns about unrelated overlaps but not groups',
      () {
    final controller = BlackboardController(animationsEnabled: false);
    addTearDown(controller.dispose);
    final base = _lessonEvents().where((event) => event.sequence <= 4).toList();
    controller.enqueueAll(base);
    expect(controller.document.unrelatedCollisions, isEmpty);

    // Layout positions are authoritative; an unrelated overlap is reported,
    // never silently “fixed” by moving the element.
    controller.enqueue(_event(
      sequence: 5,
      type: 'write_text',
      id: 'overlap',
      payload: <String, dynamic>{
        'text': 'unexpected overlap',
        'style': 'body',
        'position': _position(0.07, 0.18),
        'size': _size(0.3, 0.06),
      },
    ));
    expect(
      controller.document.warnings.any((w) => w.startsWith('layout overlap:')),
      isTrue,
    );

    final grouped = BoardDocument.empty(sessionId: 'sess_integration').applyAll(
      <BoardEvent>[
        _event(
          sequence: 1,
          type: 'write_text',
          id: 'g1',
          payload: <String, dynamic>{
            'text': 'grouped a',
            'style': 'body',
            'group_id': 'diagram',
            'position': _position(0.1, 0.1),
            'size': _size(0.2, 0.1),
          },
        ),
        _event(
          sequence: 2,
          type: 'write_text',
          id: 'g2',
          payload: <String, dynamic>{
            'text': 'grouped b',
            'style': 'body',
            'group_id': 'diagram',
            'position': _position(0.15, 0.12),
            'size': _size(0.2, 0.1),
          },
        ),
      ],
    );
    expect(grouped.collisions, isNotEmpty);
    expect(grouped.unrelatedCollisions, isEmpty);
  });

  test('small-screen transform keeps every normalized element inside bounds', () {
    final transform = BoardTransform.fromSize(const Size(390, 780));
    final document = BoardDocument.empty(sessionId: 'sess_integration')
        .applyAll(_lessonEvents());
    for (final element in document.visibleElements) {
      final bounds = element.declaredBounds ??
          BoardBoundsEstimator.estimate(element)?.clamp01();
      if (bounds == null) continue;
      final pixels = transform.rectToPixels(bounds);
      expect(pixels.left, greaterThanOrEqualTo(-1));
      expect(pixels.top, greaterThanOrEqualTo(-1));
      expect(pixels.right, lessThanOrEqualTo(391));
      expect(pixels.bottom, lessThanOrEqualTo(781));
    }
  });
}
