import 'package:flutter/material.dart';
import 'package:flutter_math_fork/flutter_math.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:pwony/blackboard/blackboard.dart';
import 'package:pwony/blackboard/model/board_transform.dart';

BoardEvent _writeText({
  required int sequence,
  required String id,
  required String text,
  required String style,
  Map<String, dynamic> extra = const <String, dynamic>{},
  double x = 0.08,
  double y = 0.08,
}) {
  return BoardEvent.fromJson(<String, dynamic>{
    'event_id': 'evt_$sequence',
    'session_id': 'sess_layout',
    'sequence': sequence,
    'type': 'write_text',
    'element_id': id,
    'payload': <String, dynamic>{
      'text': text,
      'style': style,
      'position': <String, dynamic>{'x': x, 'y': y},
      'size': <String, dynamic>{'w': 0.34, 'h': 0.08},
      ...extra,
    },
    'created_at': '2026-11-27T00:00:00.000Z',
    'board_revision': sequence,
    'animate': false,
  });
}

void main() {
  group('heading typography', () {
    test('rendered H2-H6 levels have distinct chalk specs', () {
      final fractions = <double>[];
      for (var level = 2; level <= 6; level++) {
        final spec = ChalkTextStyleSpec.forName('heading', headingLevel: level);
        expect(spec.name, 'h$level');
        expect(
          spec.heightFraction,
          BoardTextMetrics.headingFraction(level),
          reason: 'renderer and bounds estimator must agree at H$level',
        );
        fractions.add(spec.heightFraction);
      }
      for (var i = 1; i < fractions.length; i++) {
        expect(fractions[i], lessThan(fractions[i - 1]));
      }
    });

    test('legacy H1 events render at the smaller H2 size', () {
      final spec = ChalkTextStyleSpec.forName('heading', headingLevel: 1);
      expect(spec.name, 'h2');
      expect(spec.heightFraction, ChalkTextStyleSpec.forName('h2').heightFraction);
      expect(BoardTextMetrics.headingFraction(1), BoardTextMetrics.headingFraction(2));
    });

    test('h3 write_text keeps its semantic level through parsing', () {
      final event = _writeText(
        sequence: 1,
        id: 'h3',
        text: 'Deep structure',
        style: 'subheading',
        extra: <String, dynamic>{'heading_level': 3},
      );
      expect(event.headingLevel, 3);
      final document = BoardDocument.empty(sessionId: 'sess_layout').apply(event);
      expect(document['h3']?.style, 'subheading');
      expect(document['h3']?.payload['heading_level'], 3);
    });

    test('write_heading accepts levels beyond h2', () {
      final event = BoardEvent.fromJson(<String, dynamic>{
        'event_id': 'heading_6',
        'session_id': 'sess_layout',
        'sequence': 1,
        'type': 'write_heading',
        'element_id': 'h6',
        'payload': <String, dynamic>{
          'text': 'Fine print',
          'level': 6,
          'position': <String, dynamic>{'x': 0.08, 'y': 0.08},
        },
        'created_at': '2026-11-27T00:00:00.000Z',
        'board_revision': 1,
        'animate': false,
      });
      expect(event.headingLevel, 6);
      expect(event.style, 'subheading');
    });
  });

  group('equation routing', () {
    test('write_text equation carries LaTeX and routes to LaTeX rendering', () {
      const latex = r'\frac{a}{b} + \sqrt{x}';
      final event = _writeText(
        sequence: 1,
        id: 'eq1',
        text: latex,
        style: 'equation',
        extra: <String, dynamic>{'latex': latex},
      );
      expect(event.latex, latex);
      final document = BoardDocument.empty(sessionId: 'sess_layout').apply(event);
      final element = document['eq1']!;
      expect(element.isLatex, isTrue);
      expect(element.latex, latex);
    });

    testWidgets('equation is drawn by flutter_math_fork, not raw markup',
        (tester) async {
      const latex = r'E = mc^2';
      final document = BoardDocument.empty(sessionId: 'sess_layout').apply(
            _writeText(
              sequence: 1,
              id: 'eq1',
              text: latex,
              style: 'equation',
              extra: <String, dynamic>{'latex': latex},
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

      expect(find.byWidgetPredicate((widget) => widget is Math), findsOneWidget);
      expect(find.text(latex), findsNothing);
      expect(tester.takeException(), isNull);
    });
  });

  group('group-aware collision detection', () {
    BoardElement element(
      String id, {
      String? groupId,
      String? parentId,
      double x = 0.1,
      double y = 0.1,
      int sequence = 1,
    }) {
      return BoardElement(
        elementId: id,
        type: 'body',
        level: 2,
        payload: const <String, dynamic>{'text': 'body'},
        position: BoardPoint(x, y),
        size: const BoardSize(0.2, 0.1),
        groupId: groupId,
        parentId: parentId,
        createdSequence: sequence,
      );
    }

    test('unrelated overlaps are reported', () {
      final collision = BoardCollisionDetector.between(
        element('a', sequence: 1),
        element('b', x: 0.15, y: 0.12, sequence: 2),
      );
      expect(collision, isNotNull);
      expect(collision!.kind, BoardCollisionKind.unrelated);
      expect(collision.isIntentional, isFalse);
    });

    test('same-group and parent-child overlaps are intentional', () {
      final grouped = BoardCollisionDetector.between(
        element('a', groupId: 'g1'),
        element('b', groupId: 'g1', x: 0.15, y: 0.12),
      );
      expect(grouped!.kind, BoardCollisionKind.sameGroup);
      expect(grouped.isIntentional, isTrue);

      final parentChild = BoardCollisionDetector.between(
        element('parent'),
        element('child', parentId: 'parent', x: 0.15, y: 0.12),
      );
      expect(parentChild!.kind, BoardCollisionKind.parentChild);
      expect(parentChild.isIntentional, isTrue);
    });

    test('annotations overlapping their target are intentional', () {
      final target = element('target');
      final annotation = BoardElement(
        elementId: 'hl_1',
        type: 'highlight',
        level: 6,
        payload: <String, dynamic>{'target_ids': <String>['target']},
        position: const BoardPoint(0.1, 0.1),
        size: const BoardSize(0.2, 0.1),
        createdSequence: 2,
      );
      final collision = BoardCollisionDetector.between(target, annotation);
      expect(collision!.kind, BoardCollisionKind.annotationTarget);
      expect(collision.isIntentional, isTrue);
    });

    test('unrelated elements need a tiny chalk gap on mobile too', () {
      const gap = BoardCollisionDetector.minimumGap;
      final first = element('first', y: 0.1, sequence: 1);
      final tooClose = element(
        'too_close',
        y: 0.2 + gap * 0.4,
        sequence: 2,
      );
      final withGap = element('with_gap', y: 0.2 + gap, sequence: 3);

      final collision = BoardCollisionDetector.between(first, tooClose);
      expect(collision, isNotNull);
      expect(collision!.kind, BoardCollisionKind.unrelated);
      expect(
        BoardCollisionDetector.between(first, withGap),
        isNull,
        reason: 'exactly the required gap must be accepted',
      );
    });
  });

  test('BoardTransform maps normalized positions on a small screen', () {
    final transform = BoardTransform.fromSize(const Size(390, 780));
    for (final point in <BoardPoint>[
      BoardPoint.zero,
      const BoardPoint(0.5, 0.5),
      const BoardPoint(1, 1),
    ]) {
      final pixels = transform.toPixels(point);
      expect(pixels.dx, greaterThanOrEqualTo(0));
      expect(pixels.dx, lessThanOrEqualTo(390));
      expect(pixels.dy, greaterThanOrEqualTo(0));
      expect(pixels.dy, lessThanOrEqualTo(780));
      expect(transform.toNormalized(pixels), point);
    }
  });
}
