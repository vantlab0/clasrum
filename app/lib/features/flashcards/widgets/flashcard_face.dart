import 'dart:ui';

import 'package:flutter/material.dart';

import '../../../core/widgets/pwony_icons.dart';
import '../models/flashcard.dart';

enum FlashcardFaceSide { question, answer }

/// One side of a study card.
///
/// Both sides share identical dimensions and rounded geometry so flipping never
/// changes the card's footprint. Long answers scroll inside the card.
class FlashcardFace extends StatelessWidget {
  const FlashcardFace({
    super.key,
    required this.card,
    required this.side,
    required this.width,
    required this.isActive,
  });

  final Flashcard card;
  final FlashcardFaceSide side;
  final double width;
  final bool isActive;

  @override
  Widget build(BuildContext context) {
    final bool isAnswer = side == FlashcardFaceSide.answer;
    final bool compact = width < 310;
    final double radius = compact ? 24 : 27;
    final EdgeInsets padding = EdgeInsets.fromLTRB(
      compact ? 24 : 30,
      compact ? 52 : 58,
      compact ? 24 : 30,
      compact ? 26 : 32,
    );

    return DecoratedBox(
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(radius),
        boxShadow: <BoxShadow>[
          BoxShadow(
            color: Colors.black.withValues(alpha: isActive ? 0.48 : 0.28),
            blurRadius: isActive ? 42 : 28,
            offset: const Offset(0, 22),
          ),
        ],
      ),
      child: ClipRRect(
        borderRadius: BorderRadius.circular(radius),
        child: BackdropFilter(
          filter: ImageFilter.blur(sigmaX: 18, sigmaY: 18),
          child: Container(
            decoration: BoxDecoration(
              gradient: LinearGradient(
                begin: Alignment.topLeft,
                end: Alignment.bottomRight,
                colors: isAnswer
                    ? const <Color>[
                        Color(0xE61A1A1D),
                        Color(0xE6121214),
                      ]
                    : const <Color>[
                        Color(0xCC161619),
                        Color(0xD10D0D10),
                      ],
              ),
              borderRadius: BorderRadius.circular(radius),
              border: Border.all(
                color: Colors.white.withValues(
                  alpha: isActive ? 0.15 : 0.075,
                ),
                width: 0.9,
              ),
            ),
            child: Stack(
              children: <Widget>[
                Positioned(
                  top: compact ? 17 : 20,
                  right: compact ? 17 : 20,
                  child: PwonyIcon(
                    PwonyIconKind.flip,
                    size: compact ? 16 : 17,
                    strokeWidth: 1.35,
                    color:
                        Colors.white.withValues(alpha: isAnswer ? 0.3 : 0.35),
                  ),
                ),
                Positioned.fill(
                  child: Padding(
                    padding: padding,
                    child: _CardContent(
                      card: card,
                      isAnswer: isAnswer,
                      compact: compact,
                    ),
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _CardContent extends StatelessWidget {
  const _CardContent({
    required this.card,
    required this.isAnswer,
    required this.compact,
  });

  final Flashcard card;
  final bool isAnswer;
  final bool compact;

  @override
  Widget build(BuildContext context) {
    final TextStyle questionStyle = TextStyle(
      color: const Color(0xFFF4F4F5),
      fontSize: compact ? 17 : 19,
      height: 1.36,
      letterSpacing: -0.2,
      fontWeight: FontWeight.w400,
    );
    final TextStyle answerStyle = TextStyle(
      color: const Color(0xFFC5C5CC),
      fontSize: compact ? 14 : 15.5,
      height: 1.65,
      letterSpacing: 0.05,
      fontWeight: FontWeight.w300,
    );
    final TextStyle explanationStyle = TextStyle(
      color: Colors.white.withValues(alpha: 0.46),
      fontSize: compact ? 12.5 : 13.5,
      height: 1.6,
      fontWeight: FontWeight.w300,
    );

    return LayoutBuilder(
      builder: (context, constraints) {
        return SingleChildScrollView(
          physics: const ClampingScrollPhysics(),
          child: ConstrainedBox(
            constraints: BoxConstraints(
              minHeight:
                  constraints.maxHeight.isFinite ? constraints.maxHeight : 0.0,
            ),
            child: Align(
              alignment: Alignment.centerLeft,
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  if (!isAnswer)
                    Text(
                      card.question,
                      key: const ValueKey<String>('flashcard-question-text'),
                      style: questionStyle,
                    )
                  else ...<Widget>[
                    Text(
                      card.answer,
                      key: const ValueKey<String>('flashcard-answer-text'),
                      style: answerStyle,
                    ),
                    if (card.explanation != null &&
                        card.explanation!.trim().isNotEmpty) ...<Widget>[
                      const SizedBox(height: 18),
                      Text(
                        card.explanation!,
                        key: const ValueKey<String>(
                            'flashcard-explanation-text'),
                        style: explanationStyle,
                      ),
                    ],
                  ],
                ],
              ),
            ),
          ),
        );
      },
    );
  }
}
