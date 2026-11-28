import 'dart:ui' show Color;

import 'package:flutter_test/flutter_test.dart';
import 'package:pwony/blackboard/blackboard.dart';

void main() {
  final approved = ChalkPalette.illustrationColors.values.toSet();

  bool isApproved(Color color) => approved.any(
        (candidate) => candidate.toARGB32() == color.toARGB32(),
      );

  group('bright illustration palette', () {
    test('approved palette contains only vivid, high-contrast colours', () {
      expect(ChalkPalette.illustrationColors, hasLength(9));
      expect(
        ChalkPalette.illustrationColors.values,
        everyElement(isApproved),
      );
      for (final color in ChalkPalette.illustrationColors.values) {
        expect(color.a, 1.0);
        // Every approved role is visibly saturated on a true-black board.
        expect(color.r + color.g + color.b, greaterThan(1.0));
      }
    });

    test('dark, gray, off-white and unknown values cannot be rendered as diagram ink', () {
      for (final Object? raw in <Object?>[
        '#000000',
        '#111111',
        '#333333',
        '#808080',
        '#F2F0E8',
        '#A8A79E',
        'black',
        'gray',
        'offwhite',
        '',
        null,
      ]) {
        final resolved = ChalkPalette.resolveIllustration(raw);
        expect(isApproved(resolved), isTrue, reason: 'input=$raw');
      }
    });

    test('legacy chalk roles map to bright illustration roles', () {
      expect(
        ChalkPalette.resolveIllustration('primary').toARGB32(),
        ChalkPalette.illustrationColors['cyan']!.toARGB32(),
      );
      expect(
        ChalkPalette.resolveIllustration('secondary').toARGB32(),
        ChalkPalette.illustrationColors['teal']!.toARGB32(),
      );
      expect(
        ChalkPalette.resolveIllustration('red').toARGB32(),
        ChalkPalette.illustrationColors['coral']!.toARGB32(),
      );
      expect(
        ChalkPalette.resolveIllustration('green').toARGB32(),
        ChalkPalette.illustrationColors['lime']!.toARGB32(),
      );
    });

    test('teaching text still resolves to the readable chalk palette', () {
      expect(
        ChalkPalette.resolve('primary').toARGB32(),
        ChalkPalette.primary.toARGB32(),
      );
      expect(
        ChalkPalette.resolve('#F2F0E8').toARGB32(),
        ChalkPalette.primary.toARGB32(),
      );
    });

    test('index fallback yields complementary bright colours', () {
      final first = ChalkPalette.illustrationForIndex(0);
      final second = ChalkPalette.illustrationForIndex(1);
      expect(isApproved(first), isTrue);
      expect(isApproved(second), isTrue);
      expect(first.toARGB32(), isNot(second.toARGB32()));
      expect(
        ChalkPalette.illustrationForIndex(9).toARGB32(),
        first.toARGB32(),
      );
    });
  });
}
