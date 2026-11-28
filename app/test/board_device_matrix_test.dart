import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:pwony/blackboard/blackboard.dart';

BoardEvent _event(int sequence, double y) {
  return BoardEvent.fromJson(<String, dynamic>{
    'event_id': 'matrix_$sequence',
    'session_id': 'sess_matrix',
    'sequence': sequence,
    'type': 'write_text',
    'element_id': 'item_$sequence',
    'payload': <String, dynamic>{
      'text': 'Lesson step $sequence',
      'style': 'body',
      'position': <String, dynamic>{'x': 0.06, 'y': y},
      'size': <String, dynamic>{'w': 0.5, 'h': 0.07},
    },
    'created_at': '2026-11-28T00:00:00.000Z',
    'board_revision': sequence,
    'animate': false,
  });
}

void main() {
  const viewports = <Size>[
    Size(320, 568), // small phone
    Size(390, 844), // standard phone
    Size(430, 932), // large phone
    Size(844, 390), // standard phone landscape
    Size(768, 1024), // tablet portrait
    Size(1024, 768), // tablet landscape
  ];

  for (final viewport in viewports) {
    testWidgets('board renders without overflow at ${viewport.width.toInt()}x${viewport.height.toInt()}',
        (tester) async {
      final controller = BlackboardController(animationsEnabled: false);
      addTearDown(controller.dispose);
      controller.enqueueAll(<BoardEvent>[
        _event(1, 0.08),
        _event(2, 0.24),
        // A virtual tail must extend the board instead of being clamped away.
        _event(3, 1.4),
      ]);
      await controller.waitForIdle();

      await tester.pumpWidget(MaterialApp(
        home: Scaffold(
          body: SizedBox(
            width: viewport.width,
            height: viewport.height,
            child: BlackboardView(controller: controller),
          ),
        ),
      ));
      await tester.pump();

      expect(find.byType(BlackboardView), findsOneWidget);
      expect(tester.takeException(), isNull);
      final virtualBounds = controller.document.boundsOf(
        <String>{'item_3'},
        verticalExtent: 2.0,
      );
      expect(virtualBounds, isNotNull);
      expect(virtualBounds!.y, closeTo(1.4, 0.0001));
    });
  }
}
