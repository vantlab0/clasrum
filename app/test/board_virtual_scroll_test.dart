import 'dart:ui' show Offset, Size;

import 'package:flutter_test/flutter_test.dart';
import 'package:pwony/blackboard/blackboard.dart';
import 'package:pwony/blackboard/model/board_transform.dart';
import 'package:pwony/blackboard/runtime/board_viewport_controller.dart';

BoardEvent _textEvent({
  required int sequence,
  required String id,
  required double y,
  String groupId = '',
}) {
  return BoardEvent.fromJson(<String, dynamic>{
    'event_id': 'evt_$sequence',
    'session_id': 'sess_virtual',
    'sequence': sequence,
    'type': 'write_text',
    'element_id': id,
    'payload': <String, dynamic>{
      'text': 'item $id',
      'style': 'body',
      'position': <String, dynamic>{'x': 0.1, 'y': y},
      'size': <String, dynamic>{'w': 0.3, 'h': 0.08},
      if (groupId.isNotEmpty) 'group_id': groupId,
    },
    'created_at': '2026-11-28T00:00:00.000Z',
    'board_revision': sequence,
    'animate': false,
  });
}

void main() {
  group('virtual vertical scroll', () {
    test('logical y>1 is not clamped by boundsOf when extent allows it', () {
      final doc = BoardDocument.empty(sessionId: 'sess_virtual').applyAll(<BoardEvent>[
        _textEvent(sequence: 1, id: 'a', y: 1.4),
      ]);
      final bounds = doc.boundsOf(<String>['a'], verticalExtent: 2.0);
      expect(bounds, isNotNull);
      expect(bounds!.y, closeTo(1.4, 0.0001));
      expect(bounds.bottom, greaterThan(1.0));
    });

    test('transform visible rect and clamp honor logical extent', () {
      final t = BoardTransform.fromSize(
        const Size(400, 400),
        logicalExtent: 2.5,
      ).withViewport(offset: const Offset(0, 1.8), scale: 1.0);
      final visible = t.visibleLogicalRect;
      expect(visible.bottom, lessThanOrEqualTo(2.5 + 0.0001));
      expect(visible.y, greaterThan(0.5));
      final clamped = BoardTransform.clampOffset(
        const Offset(0, 10),
        scale: 1.0,
        logicalExtent: 2.5,
      );
      expect(clamped.dy, closeTo(1.5, 0.0001));
    });

    test('virtual bottom is reachable at normal zoom', () {
      final controller = BoardViewportController();
      addTearDown(controller.dispose);
      controller.onViewportSizeChanged(const Size(400, 800));
      controller.setViewport(Offset.zero, 1.0);
      controller.onContentBoundsChanged(const BoardRect(0, 0, 1, 2));
      controller.setViewport(const Offset(0, 10), 1.0);

      expect(controller.visibleRect.bottom, closeTo(controller.contentExtent, 0.001));
    });

    test('same-group reflow displacement is atomic', () {
      final doc = BoardDocument.empty(sessionId: 'sess_virtual').applyAll(<BoardEvent>[
        _textEvent(sequence: 1, id: 'n1', y: 0.2, groupId: 'g1'),
        _textEvent(sequence: 2, id: 'n2', y: 0.25, groupId: 'g1'),
      ]);
      final positions = BoardVerticalLayout.positions(
        document: doc,
        narrow: true,
        boundsOf: (e) => BoardRect(e.position!.x, e.position!.y, 0.2, 0.08),
      );
      // Atomic block displacement: relative offsets inside the group preserved.
      expect(positions['n1']!.y - 0.2, closeTo(positions['n2']!.y - 0.25, 0.0001));
      expect(positions['n2']!.x - positions['n1']!.x, closeTo(0.0, 0.0001));
    });
  });
}
