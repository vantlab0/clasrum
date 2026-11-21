import 'package:flutter/material.dart';

/// The thin, minimal progress rail shown above the deck.
class FlashcardProgressIndicator extends StatelessWidget {
  const FlashcardProgressIndicator({
    super.key,
    required this.progress,
    required this.currentIndex,
    required this.total,
  });

  final double progress;
  final int currentIndex;
  final int total;

  @override
  Widget build(BuildContext context) {
    final double clamped =
        progress.isFinite ? progress.clamp(0.0, 1.0).toDouble() : 0.0;
    return Semantics(
      label: 'Flashcard progress',
      value: 'Card ${currentIndex + 1} of $total',
      child: SizedBox(
        height: 3,
        child: TweenAnimationBuilder<double>(
          tween: Tween<double>(end: clamped),
          duration: const Duration(milliseconds: 420),
          curve: Curves.easeOutCubic,
          builder: (context, value, _) {
            return ClipRRect(
              borderRadius: BorderRadius.circular(999),
              child: Stack(
                fit: StackFit.expand,
                children: <Widget>[
                  ColoredBox(
                    color: Colors.white.withValues(alpha: 0.09),
                  ),
                  FractionallySizedBox(
                    alignment: Alignment.centerLeft,
                    widthFactor: value,
                    child: DecoratedBox(
                      decoration: BoxDecoration(
                        gradient: LinearGradient(
                          colors: <Color>[
                            Colors.white.withValues(alpha: 0.32),
                            Colors.white.withValues(alpha: 0.92),
                          ],
                        ),
                      ),
                    ),
                  ),
                ],
              ),
            );
          },
        ),
      ),
    );
  }
}
