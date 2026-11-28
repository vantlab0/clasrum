import 'dart:ui' show Size;

import 'package:flutter_test/flutter_test.dart';
import 'package:pwony/blackboard/blackboard.dart';

BoardEvent _event({
  required int sequence,
  required String id,
  required double x,
  required double y,
  double width = 0.3,
  double height = 0.08,
  String type = 'write_text',
  String? groupId,
  String? parentId,
}) {
  return BoardEvent.fromJson(<String, dynamic>{
    'event_id': 'evt_$sequence',
    'session_id': 'sess_vertical',
    'sequence': sequence,
    'type': type,
    'element_id': id,
    'payload': <String, dynamic>{
      'text': 'line $sequence',
      'style': 'body',
      'position': <String, dynamic>{'x': x, 'y': y},
      'size': <String, dynamic>{'w': width, 'h': height},
      if (groupId != null) 'group_id': groupId,
      if (parentId != null) 'parent_id': parentId,
    },
    'created_at': '2026-11-27T00:00:00.000Z',
    'board_revision': sequence,
    'animate': false,
  });
}

BoardDocument _document(List<BoardEvent> events) =>
    BoardDocument.empty(sessionId: 'sess_vertical').applyAll(events);

void main() {
  group('BoardVerticalLayout', () {
    test('narrow detection uses the portrait aspect threshold', () {
      expect(BoardVerticalLayout.isNarrow(const Size(390, 780)), isTrue);
      expect(BoardVerticalLayout.isNarrow(const Size(820, 780)), isFalse);
    });

    test('desktop keeps authoritative positions untouched', () {
      final document = _document(<BoardEvent>[
        _event(sequence: 1, id: 'a', x: 0.5, y: 0.2),
      ]);
      expect(
        BoardVerticalLayout.positions(
          document: document,
          narrow: false,
          boundsOf: (_) => null,
        ),
        isEmpty,
      );
    });

    test('narrow text stacks by original y with measured gaps', () {
      final heights = <String, double>{
        'late': 0.10,
        'first': 0.07,
        'middle': 0.12,
      };
      final document = _document(<BoardEvent>[
        _event(sequence: 1, id: 'late', x: 0.1, y: 0.62),
        _event(sequence: 2, id: 'first', x: 0.55, y: 0.08),
        _event(sequence: 3, id: 'middle', x: 0.3, y: 0.35),
      ]);
      final positions = BoardVerticalLayout.positions(
        document: document,
        narrow: true,
        boundsOf: (element) => BoardRect(
          element.position!.x,
          element.position!.y,
          0.3,
          heights[element.elementId] ?? 0.08,
        ),
      );

      expect(positions['first']!.y, lessThan(positions['middle']!.y));
      expect(positions['middle']!.y, lessThan(positions['late']!.y));
      expect(positions['first']!.x,
          greaterThanOrEqualTo(BoardVerticalLayout.sideMargin));
      expect(positions['late']!.x + 0.3,
          lessThanOrEqualTo(1 - BoardVerticalLayout.sideMargin + 0.0001));

      final firstBottom =
          positions['first']!.y + heights['first']!;
      expect(positions['middle']!.y - firstBottom,
          closeTo(BoardVerticalLayout.blockGap, 0.0001));
    });

    test('overflow keeps stacking instead of reverting to original y', () {
      final document = _document(<BoardEvent>[
        _event(sequence: 1, id: 'a', x: 0.1, y: 0.1, height: 0.5),
        _event(sequence: 2, id: 'b', x: 0.1, y: 0.2, height: 0.5),
        _event(sequence: 3, id: 'c', x: 0.1, y: 0.3, height: 0.5),
      ]);
      final positions = BoardVerticalLayout.positions(
        document: document,
        narrow: true,
        boundsOf: (element) => BoardRect(
          element.position!.x,
          element.position!.y,
          0.3,
          0.5,
        ),
      );
      expect(positions['a']!.y, lessThan(positions['b']!.y));
      expect(positions['b']!.y, lessThan(positions['c']!.y));
      expect(positions['c']!.y, greaterThan(1.0));
    });

    test('same-group elements move as one block', () {
      final document = _document(<BoardEvent>[
        _event(
          sequence: 1,
          id: 'node_a',
          x: 0.2,
          y: 0.1,
          groupId: 'diagram_1',
        ),
        _event(
          sequence: 2,
          id: 'node_b',
          x: 0.45,
          y: 0.1,
          groupId: 'diagram_1',
        ),
      ]);
      final positions = BoardVerticalLayout.positions(
        document: document,
        narrow: true,
        boundsOf: (element) => BoardRect(
          element.position!.x,
          element.position!.y,
          0.2,
          0.08,
        ),
      );
      expect(positions['node_a']!.y, positions['node_b']!.y);
      expect(
        positions['node_b']!.x - positions['node_a']!.x,
        closeTo(0.25, 0.0001),
      );
    });

    test('children follow their parent block displacement', () {
      final document = _document(<BoardEvent>[
        _event(
          sequence: 1,
          id: 'axes',
          x: 0.4,
          y: 0.1,
        ),
        _event(
          sequence: 2,
          id: 'label',
          x: 0.55,
          y: 0.14,
          parentId: 'axes',
        ),
      ]);
      final positions = BoardVerticalLayout.positions(
        document: document,
        narrow: true,
        boundsOf: (element) => BoardRect(
          element.position!.x,
          element.position!.y,
          0.2,
          0.08,
        ),
      );
      final parentDelta = positions['axes']!.x - 0.4;
      expect(
        positions['label']!.x - 0.55,
        closeTo(parentDelta, 0.0001),
      );
    });

    test('nested parent-child labels inherit the full displaced chain', () {
      final document = _document(<BoardEvent>[
        _event(
          sequence: 1,
          id: 'stage',
          x: 0.5,
          y: 0.1,
          groupId: 'cycle',
        ),
        _event(
          sequence: 2,
          id: 'node',
          x: 0.55,
          y: 0.12,
          groupId: 'cycle',
          parentId: 'stage',
        ),
        _event(
          sequence: 3,
          id: 'tick',
          x: 0.6,
          y: 0.14,
          groupId: 'cycle',
          parentId: 'node',
        ),
      ]);
      final positions = BoardVerticalLayout.positions(
        document: document,
        narrow: true,
        boundsOf: (element) => BoardRect(
          element.position!.x,
          element.position!.y,
          0.2,
          0.08,
        ),
      );
      final stageDelta = positions['stage']!.y - 0.1;
      expect(positions['node']!.y - 0.12, closeTo(stageDelta, 0.0001));
      expect(positions['tick']!.y - 0.14, closeTo(stageDelta, 0.0001));
    });

    test('grouped child of an ungrouped parent keeps its own group block', () {
      final document = _document(<BoardEvent>[
        _event(sequence: 1, id: 'axes', x: 0.4, y: 0.1),
        _event(
          sequence: 2,
          id: 'label',
          x: 0.55,
          y: 0.14,
          groupId: 'labels',
          parentId: 'axes',
        ),
      ]);
      final positions = BoardVerticalLayout.positions(
        document: document,
        narrow: true,
        boundsOf: (element) => BoardRect(
          element.position!.x,
          element.position!.y,
          0.2,
          0.08,
        ),
      );
      final parentDelta = positions['axes']!.y - 0.1;
      expect(positions['label']!.y - 0.14, isNot(closeTo(parentDelta, 0.0001)));
      expect(
        positions['label']!.y,
        closeTo(BoardVerticalLayout.topMargin +
            0.08 +
            BoardVerticalLayout.blockGap,
            0.0001),
      );
    });
  });
}
