import 'package:flutter/material.dart';
import 'package:flutter_math_fork/flutter_math.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:pwony/blackboard/blackboard.dart';

BoardEvent _event(int sequence, String type, String id, Map<String, dynamic> payload) {
  return BoardEvent.fromJson(<String, dynamic>{
    'event_id': 'mobile_$sequence',
    'session_id': 'sess_mobile',
    'sequence': sequence,
    'type': type,
    'element_id': id,
    'payload': payload,
    'created_at': '2026-11-27T00:00:00.000Z',
    'board_revision': sequence,
    'animate': false,
  });
}

void main() {
  testWidgets('mobile screen renders the persistent board without overflow',
      (tester) async {
    const latex = r'\frac{F}{m} = a';
    final document = BoardDocument.empty(sessionId: 'sess_mobile').applyAll(
      <BoardEvent>[
        _event(1, 'write_text', 'h2', <String, dynamic>{
          'text': 'Dynamics',
          'style': 'subheading',
          'heading_level': 2,
          'position': <String, dynamic>{'x': 0.05, 'y': 0.05},
          'size': <String, dynamic>{'w': 0.5, 'h': 0.07},
        }),
        _event(2, 'write_text', 'eq', <String, dynamic>{
          'text': latex,
          'style': 'equation',
          'latex': latex,
          'position': <String, dynamic>{'x': 0.08, 'y': 0.22},
          'size': <String, dynamic>{'w': 0.3, 'h': 0.08},
        }),
      ],
    );
    final controller = BlackboardController(
      animationsEnabled: false,
      initialDocument: document,
    );
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

    final mathFinder = find.byWidgetPredicate((widget) => widget is Math);
    expect(mathFinder, findsOneWidget);
    final topLeft = tester.getTopLeft(mathFinder);
    expect(topLeft.dx, greaterThanOrEqualTo(0));
    expect(topLeft.dx, lessThan(390));
    expect(topLeft.dy, greaterThanOrEqualTo(0));
    expect(topLeft.dy, lessThan(780));
    expect(tester.takeException(), isNull);
  });
}
