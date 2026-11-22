import 'dart:ui' as ui;

import 'package:flutter_test/flutter_test.dart';
import 'package:pwony/blackboard/blackboard.dart';
import 'package:pwony/blackboard/model/board_transform.dart';
import 'package:pwony/blackboard/render/generative_renderer.dart';

Map<String, dynamic> _point(double x, double y) => {'x': x, 'y': y};

Map<String, dynamic> _basePlan({List<Map<String, dynamic>>? elements, List<Map<String, dynamic>>? stages}) {
  return {
    'protocol': 'clasrum.drawing',
    'version': 1,
    'title': 'Test plan',
    'elements': elements ?? [
      {
        'id': 'body',
        'kind': 'ellipse',
        'geometry': {'center': _point(0.5, 0.5), 'rx': 0.2, 'ry': 0.15},
        'style': {
          'stroke_color': 'green',
          'stroke_width': 2.0,
          'stroke_opacity': 0.9,
          'fill_color': 'green',
          'fill_opacity': 0.2,
          'line_cap': 'round',
          'line_join': 'round',
          'dash': [4.0, 2.0],
        },
        'layer': 0,
        'stage': 1,
        'duration_ms': 700,
      },
      {
        'id': 'tail',
        'kind': 'cubic_bezier',
        'geometry': {
          'start': _point(0.7, 0.5),
          'c1': _point(0.8, 0.4),
          'c2': _point(0.85, 0.7),
          'end': _point(0.9, 0.6),
        },
        'style': {'stroke_color': 'primary', 'stroke_width': 1.2},
        'layer': 1,
        'stage': 2,
        'duration_ms': 400,
        'delay_ms': 120,
      },
      {
        'id': 'label',
        'kind': 'text',
        'geometry': {'text': 'nucleus', 'position': _point(0.45, 0.48), 'align': 'center'},
        'layer': 2,
        'stage': 3,
        'duration_ms': 250,
      },
    ],
    'stages': stages ?? [
      {'stage': 1, 'description': 'body', 'element_ids': ['body'], 'pause_ms': 150},
      {'stage': 2, 'description': 'tail', 'element_ids': ['tail']},
      {'stage': 3, 'description': 'labels', 'element_ids': ['label']},
    ],
  };
}

Map<String, dynamic> _drawPlanEvent({required String eventId, required int sequence, Map<String, dynamic>? plan}) {
  return {
    'event_id': eventId,
    'session_id': 'sess_1',
    'sequence': sequence,
    'type': 'draw_plan',
    'element_id': 'dp_$sequence',
    'payload': plan ?? _basePlan(),
    'created_at': '2026-11-22T00:00:00.000Z',
    'board_revision': sequence,
    'depends_on': <String>[],
    'animate': false,
  };
}

void main() {
  group('DrawingPlan parsing', () {
    test('parses valid plan and round trips', () {
      final plan = DrawingPlan.parse(_basePlan());
      expect(plan.protocol, 'clasrum.drawing');
      expect(plan.version, 1);
      expect(plan.elements.length, 3);
      final json = plan.toJson();
      final reparsed = DrawingPlan.parse(json);
      expect(reparsed.toJson(), json);
    });

    test('parses every geometry variant', () {
      final geometries = <String, Map<String, dynamic>>{
        'line': {'start': _point(0.1, 0.1), 'end': _point(0.5, 0.5)},
        'polyline': {'points': [_point(0.1, 0.1), _point(0.4, 0.3), _point(0.6, 0.5)]},
        'polygon': {'points': [_point(0.1, 0.1), _point(0.5, 0.1), _point(0.3, 0.5)], 'closed': true},
        'rectangle': {'position': _point(0.2, 0.2), 'size': {'w': 0.3, 'h': 0.2}, 'corner_radius': 0.02},
        'ellipse': {'center': _point(0.5, 0.5), 'rx': 0.2, 'ry': 0.1},
        'arc': {'center': _point(0.5, 0.5), 'rx': 0.2, 'ry': 0.2, 'start_angle': 0.0, 'sweep_angle': 90.0},
        'quad_bezier': {'start': _point(0.1, 0.5), 'control': _point(0.5, 0.1), 'end': _point(0.9, 0.5)},
        'cubic_bezier': {'start': _point(0.1, 0.5), 'c1': _point(0.3, 0.1), 'c2': _point(0.7, 0.9), 'end': _point(0.9, 0.5)},
        'path': {
          'segments': [
            {'op': 'move', 'point': _point(0.1, 0.1)},
            {'op': 'line', 'point': _point(0.5, 0.5)},
            {'op': 'quad', 'control': _point(0.6, 0.2), 'point': _point(0.8, 0.4)},
            {'op': 'cubic', 'c1': _point(0.7, 0.6), 'c2': _point(0.9, 0.7), 'point': _point(0.95, 0.5)},
            {'op': 'close'},
          ]
        },
        'text': {'text': 'hello', 'position': _point(0.5, 0.5), 'align': 'center'},
        'arrow': {'start': _point(0.1, 0.1), 'end': _point(0.5, 0.5), 'curve': 0.2},
      };

      for (final entry in geometries.entries) {
        final elements = <Map<String, dynamic>>[
          {
            'id': 'e1',
            'kind': entry.key,
            'geometry': entry.value,
            'stage': 1,
            'duration_ms': 300,
          },
        ];
        if (entry.key == 'text') {
          elements.add({
            'id': 'backdrop',
            'kind': 'line',
            'geometry': {'start': _point(0.1, 0.1), 'end': _point(0.5, 0.5)},
            'stage': 1,
            'duration_ms': 300,
          });
        }
        final plan = DrawingPlan.parse({
          'protocol': 'clasrum.drawing',
          'version': 1,
          'elements': elements,
          'stages': [
            {'stage': 1, 'element_ids': elements.map((e) => e['id']).toList()}
          ],
        });
        expect(plan.elements.first.kind.wireName, entry.key);
      }
    });

    test('rejects invalid and missing properties', () {
      expect(() => DrawingPlan.parse({'protocol': 'clasrum.drawing', 'version': 1, 'elements': []}), throwsA(isA<DrawingPlanParseException>()));
      expect(
        () => DrawingPlan.parse(_basePlan(elements: [
          {
            'id': 'bad',
            'kind': 'ellipse',
            'geometry': {'center': _point(0.5, 0.5), 'rx': 1.5, 'ry': 0.1},
          }
        ])),
        throwsA(isA<DrawingPlanParseException>()),
      );
      expect(
        () => DrawingPlan.parse(_basePlan(elements: [
          {
            'id': 'bad',
            'kind': 'cubic_bezier',
            'geometry': {'start': _point(0, 0), 'end': _point(1, 0)},
          }
        ])),
        throwsA(isA<DrawingPlanParseException>()),
      );
      expect(
        () => DrawingPlan.parse(_basePlan(elements: [
          {
            'id': 'bad',
            'kind': 'text',
            'geometry': {'text': 'ok\x07', 'position': _point(0.1, 0.1)},
          },
          {
            'id': 'ok',
            'kind': 'line',
            'geometry': {'start': _point(0, 0), 'end': _point(1, 1)},
          }
        ])),
        throwsA(isA<DrawingPlanParseException>()),
      );
    });
  });

  group('Styles, opacity, dash, bounds', () {
    test('parses stroke/fill/opacity/dash/line caps/joins', () {
      final plan = DrawingPlan.parse(_basePlan());
      final style = plan.elements.first.style!;
      expect(style.strokeColor, 'green');
      expect(style.strokeWidth, 2.0);
      expect(style.strokeOpacity, 0.9);
      expect(style.fillColor, 'green');
      expect(style.fillOpacity, 0.2);
      expect(style.lineCap, 'round');
      expect(style.lineJoin, 'round');
      expect(style.dash, [4.0, 2.0]);
    });

    test('bounds calculation covers elements', () {
      final plan = DrawingPlan.parse(_basePlan());
      final bounds = plan.elementBounds;
      expect(bounds.length, 3);
      expect(bounds[0].x, closeTo(0.3, 0.0001));
      expect(bounds[0].y, closeTo(0.35, 0.0001));
      expect(bounds[0].w, closeTo(0.4, 0.0001));
      expect(bounds[0].h, closeTo(0.3, 0.0001));
    });

    test('fill on open element is rejected', () {
      expect(
        () => DrawingPlan.parse(_basePlan(elements: [
          {
            'id': 'line1',
            'kind': 'line',
            'geometry': {'start': _point(0, 0), 'end': _point(1, 1)},
            'style': {'fill_color': 'red'},
          }
        ])),
        throwsA(isA<DrawingPlanParseException>()),
      );
    });
  });

  group('Timeline and stages', () {
    test('computes total duration with stages, delays, pauses', () {
      final plan = DrawingPlan.parse(_basePlan());
      // stage1: 700; pause 150; stage2: delay 120 + 400; stage3: 250
      expect(plan.totalDurationMs, 700 + 150 + 120 + 400 + 250);
    });

    test('timeline respects stage ordering and explicit element_ids', () {
      final plan = DrawingPlan.parse(_basePlan());
      final timelines = computeElementTimelines(plan);
      expect(timelines.map((t) => t.elementId).toList(), ['body', 'tail', 'label']);
      expect(timelines[0].startMs, 0);
      expect(timelines[0].endMs, 700);
      expect(timelines[1].startMs, 700 + 150 + 120);
      expect(timelines[2].startMs, 700 + 150 + 120 + 400);
    });

    test('layer ordering is preserved in element data', () {
      final plan = DrawingPlan.parse(_basePlan());
      expect(plan.elements[0].layer, 0);
      expect(plan.elements[1].layer, 1);
      expect(plan.elements[2].layer, 2);
    });
  });

  group('Renderer', () {
    test('progressive reveal renders partial then full', () {
      final plan = DrawingPlan.parse(_basePlan());
      final transform = BoardTransform.fromSize(const ui.Size(800, 600));
      final recorder1 = ui.PictureRecorder();
      final canvas1 = ui.Canvas(recorder1);
      final r1 = GenerativeDrawingRenderer.render(canvas1, plan, transform, progress: 0.0);
      recorder1.endRecording();
      expect(r1.elementBounds, isEmpty);

      final recorder2 = ui.PictureRecorder();
      final canvas2 = ui.Canvas(recorder2);
      final r2 = GenerativeDrawingRenderer.render(canvas2, plan, transform, progress: 0.5);
      recorder2.endRecording();
      expect(r2.elementBounds.isNotEmpty, isTrue);

      final recorder3 = ui.PictureRecorder();
      final canvas3 = ui.Canvas(recorder3);
      final r3 = GenerativeDrawingRenderer.render(canvas3, plan, transform, progress: 1.0);
      recorder3.endRecording();
      expect(r3.elementBounds.length, 3);
    });
  });

  group('Board event integration', () {
    test('parses draw_plan event', () {
      final event = BoardEvent.fromJson(_drawPlanEvent(eventId: 'e1', sequence: 1));
      expect(event.kind, BoardEventType.drawPlan);
      expect(event.isWellFormed, isTrue);
      expect(event.payloadParsed.isValid, isTrue);
      expect(event.payloadParsed, isA<DrawPlanPayload>());
      final payload = event.payloadParsed as DrawPlanPayload;
      expect(payload.plan, isNotNull);
      expect(payload.plan!.elements.length, 3);
    });

    test('board document applies draw_plan and replay preserves it', () {
      final doc = BoardDocument.empty(sessionId: 'sess_1');
      final event = BoardEvent.fromJson(_drawPlanEvent(eventId: 'e1', sequence: 1));
      final applied = doc.apply(event);
      expect(applied.visibleElements.length, 1);
      final element = applied.visibleElements.first;
      expect(element.type, 'draw_plan');
      expect(element.animation['duration_ms'], isA<int>());

      final replayed = BoardDocument.empty(sessionId: 'sess_1').replay([event]);
      expect(replayed.visibleElements.length, 1);
      expect(replayed.visibleElements.first.type, 'draw_plan');
    });

    test('cancellation and replacement via clear/erase', () {
      var doc = BoardDocument.empty(sessionId: 'sess_1');
      doc = doc.apply(BoardEvent.fromJson(_drawPlanEvent(eventId: 'e1', sequence: 1)));
      expect(doc.visibleElements.length, 1);

      // Replace by adding another plan
      doc = doc.apply(BoardEvent.fromJson(_drawPlanEvent(eventId: 'e2', sequence: 2)));
      expect(doc.visibleElements.length, 2);

      // Clear
      doc = doc.apply(BoardEvent.fromJson({
        'event_id': 'e3',
        'session_id': 'sess_1',
        'sequence': 3,
        'type': 'clear',
        'element_id': null,
        'payload': {},
        'created_at': '2026-11-22T00:00:00.000Z',
        'board_revision': 3,
        'depends_on': <String>[],
        'animate': false,
      }));
      expect(doc.visibleElements, isEmpty);
    });
  });
}