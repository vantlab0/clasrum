import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../models/flashcard.dart';
import 'flashcard_face.dart';

/// A single card in the deck. Inactive neighbours are non-interactive previews
/// and always show their question side.
class FlashcardStackItem extends StatelessWidget {
  const FlashcardStackItem({
    super.key,
    required this.card,
    required this.width,
    required this.height,
    required this.isActive,
    this.flipAnimation,
    this.onTap,
  });

  final Flashcard card;
  final double width;
  final double height;
  final bool isActive;
  final Animation<double>? flipAnimation;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final Widget front = SizedBox(
      width: width,
      height: height,
      child: FlashcardFace(
        key: const ValueKey<String>('flashcard-front'),
        card: card,
        side: FlashcardFaceSide.question,
        width: width,
        isActive: isActive,
      ),
    );

    final Widget content;
    if (isActive && flipAnimation != null) {
      content = AnimatedBuilder(
        animation: flipAnimation!,
        child: front,
        builder: (context, child) {
          final double value = flipAnimation!.value.clamp(0.0, 1.0).toDouble();
          final double angle = value * math.pi;
          final bool showBack = value > 0.5;
          final Matrix4 transform = Matrix4.identity()
            ..setEntry(3, 2, 0.0014)
            ..rotateY(showBack ? angle - math.pi : angle);

          return Transform(
            alignment: Alignment.center,
            transform: transform,
            child: showBack
                ? SizedBox(
                    width: width,
                    height: height,
                    child: FlashcardFace(
                      key: const ValueKey<String>('flashcard-back'),
                      card: card,
                      side: FlashcardFaceSide.answer,
                      width: width,
                      isActive: isActive,
                    ),
                  )
                : child,
          );
        },
      );
    } else {
      content = front;
    }

    if (!isActive) {
      return IgnorePointer(
        child: ExcludeSemantics(child: content),
      );
    }

    return Semantics(
      button: true,
      label: 'Active flashcard. Tap to flip.',
      child: GestureDetector(
        behavior: HitTestBehavior.opaque,
        onTap: onTap,
        child: content,
      ),
    );
  }
}
