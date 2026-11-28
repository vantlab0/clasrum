import 'package:flutter_test/flutter_test.dart';
import 'package:pwony/blackboard/model/board_geometry.dart';
import 'package:pwony/blackboard/runtime/board_fullness_detector.dart';

void main() {
  const visible = BoardRect(0, 0, 1, 1);

  group('BoardFullnessDetector', () {
    test('tiles of live measured boxes count as a full visible screen', () {
      final snapshot = BoardFullnessDetector.snapshot(
        visibleRect: visible,
        elementBounds: const <BoardRect>[
          BoardRect(0, 0, 0.5, 0.6),
          BoardRect(0.5, 0, 0.5, 0.6),
          BoardRect(0, 0.6, 1, 0.3),
        ],
        sequence: 7,
      );
      expect(snapshot.intersectingElementCount, 3);
      expect(snapshot.coverage, closeTo(0.9, 0.001));
      expect(snapshot.isFull, isTrue);
      expect(snapshot.toJson()['w'], 1.0);
    });

    test('one oversized element does not count as a filled lesson board', () {
      final snapshot = BoardFullnessDetector.snapshot(
        visibleRect: visible,
        elementBounds: const <BoardRect>[BoardRect(0, 0, 1, 1)],
        sequence: 1,
      );
      expect(snapshot.coverage, 1.0);
      expect(snapshot.isFull, isFalse);
    });

    test('a zoomed view into empty space is not full', () {
      final snapshot = BoardFullnessDetector.snapshot(
        visibleRect: const BoardRect(0.5, 0, 0.5, 1),
        elementBounds: const <BoardRect>[
          BoardRect(0, 0, 0.4, 0.4),
          BoardRect(0, 0.4, 0.4, 0.4),
          BoardRect(0, 0.8, 0.4, 0.2),
        ],
        sequence: 2,
      );
      expect(snapshot.coverage, 0.0);
      expect(snapshot.isFull, isFalse);
    });

    test('virtual extent does not force a clear before bottom', () {
      final snapshot = BoardFullnessDetector.snapshot(
        visibleRect: const BoardRect(0, 0, 1, 1),
        elementBounds: const <BoardRect>[
          BoardRect(0, 0, 1, 0.4),
          BoardRect(0, 0.4, 1, 0.4),
          BoardRect(0, 1.2, 1, 0.3),
        ],
        sequence: 8,
        contentExtent: 2.0,
      );
      expect(snapshot.isFull, isFalse);
      expect(snapshot.reachedBottom, isFalse);
    });

    test('reports reached bottom at virtual end', () {
      final snapshot = BoardFullnessDetector.snapshot(
        visibleRect: const BoardRect(0, 1.2, 1, 1),
        elementBounds: const <BoardRect>[
          BoardRect(0, 1.2, 1, 0.3),
          BoardRect(0, 1.5, 1, 0.3),
          BoardRect(0, 1.8, 1, 0.3),
        ],
        sequence: 9,
        contentExtent: 2.2,
      );
      expect(snapshot.reachedBottom, isTrue);
    });

    test('once latched, coverage must drop below the release threshold', () {
      final boxes = <BoardRect>[
        const BoardRect(0, 0, 0.4, 1),
        const BoardRect(0.4, 0, 0.3, 1),
      ];
      final stillFull = BoardFullnessDetector.snapshot(
        visibleRect: visible,
        elementBounds: boxes,
        sequence: 3,
        wasFull: true,
      );
      expect(stillFull.coverage, closeTo(0.7, 0.001));
      expect(stillFull.isFull, isTrue);

      final rearmed = BoardFullnessDetector.snapshot(
        visibleRect: visible,
        elementBounds: const <BoardRect>[
          BoardRect(0, 0, 0.3, 1),
          BoardRect(0.3, 0, 0.2, 1),
        ],
        sequence: 4,
        wasFull: true,
      );
      expect(rearmed.coverage, closeTo(0.5, 0.001));
      expect(rearmed.isFull, isFalse);
    });
  });
}
