import 'dart:async';
import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../models/flashcard.dart';
import 'flashcard_stack_item.dart';

/// Horizontally draggable stack of flashcard previews.
///
/// Only the active card and its immediate neighbours are built. The active card
/// follows the finger; the neighbours shift and fade in response to the drag.
class FlashcardDeck extends StatefulWidget {
  const FlashcardDeck({
    super.key,
    required this.cards,
    required this.currentIndex,
    required this.flipAnimation,
    required this.onFlip,
    required this.onNavigate,
  });

  final List<Flashcard> cards;
  final int currentIndex;
  final Animation<double> flipAnimation;
  final VoidCallback onFlip;
  final ValueChanged<int> onNavigate;

  @override
  State<FlashcardDeck> createState() => _FlashcardDeckState();
}

class _FlashcardDeckState extends State<FlashcardDeck> {
  static const Duration _settleDuration = Duration(milliseconds: 330);

  double _dragOffset = 0;
  bool _dragging = false;
  bool _settling = false;
  Timer? _settleTimer;

  @override
  void dispose() {
    _settleTimer?.cancel();
    super.dispose();
  }

  void _onDragStart(DragStartDetails details) {
    _settleTimer?.cancel();
    setState(() {
      _dragging = true;
      _settling = false;
      _dragOffset = 0;
    });
  }

  void _onDragUpdate(DragUpdateDetails details, double cardWidth) {
    if (!_dragging) return;
    final double limit = cardWidth * 0.62;
    double next = _dragOffset + details.delta.dx;
    if (next.abs() > limit) {
      final double overflow = next.abs() - limit;
      next = next.sign * (limit + overflow * 0.14);
    }
    setState(() => _dragOffset = next);
  }

  void _onDragEnd(DragEndDetails details) {
    if (!_dragging) return;
    final double velocity = details.velocity.pixelsPerSecond.dx;
    final double distance = _dragOffset;

    final bool nextByVelocity = velocity <= -520;
    final bool nextByDistance = distance <= -60 && velocity <= 180;
    final bool previousByVelocity = velocity >= 520;
    final bool previousByDistance = distance >= 60 && velocity >= -180;

    final int lastIndex = widget.cards.length - 1;
    if ((nextByVelocity || nextByDistance) && widget.currentIndex < lastIndex) {
      _navigateTo(widget.currentIndex + 1);
      return;
    }
    if ((previousByVelocity || previousByDistance) && widget.currentIndex > 0) {
      _navigateTo(widget.currentIndex - 1);
      return;
    }
    _snapBack();
  }

  void _onDragCancel() {
    if (_dragging) _snapBack();
  }

  void _navigateTo(int index) {
    _settleTimer?.cancel();
    setState(() {
      _dragging = false;
      _settling = true;
      _dragOffset = 0;
    });
    _settleTimer = Timer(_settleDuration, () {
      if (mounted) setState(() => _settling = false);
    });
    widget.onNavigate(index);
  }

  void _snapBack() {
    _settleTimer?.cancel();
    setState(() {
      _dragging = false;
      _settling = true;
      _dragOffset = 0;
    });
    _settleTimer = Timer(_settleDuration, () {
      if (mounted) setState(() => _settling = false);
    });
  }

  void _handleTap() {
    if (_dragging || _settling || widget.flipAnimation.isAnimating) return;
    widget.onFlip();
  }

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        final _DeckMetrics metrics = _DeckMetrics.from(constraints);
        final double dragProgress =
            (_dragOffset.abs() / (metrics.cardWidth * 0.55))
                .clamp(0.0, 1.0)
                .toDouble();
        final Duration positionDuration =
            _dragging ? Duration.zero : _settleDuration;

        final int first = math.max(0, widget.currentIndex - 1);
        final int last =
            math.min(widget.cards.length - 1, widget.currentIndex + 1);
        final List<int> visible = <int>[
          for (int index = first; index <= last; index++) index,
        ]..sort((a, b) {
            final int distanceA = (a - widget.currentIndex).abs();
            final int distanceB = (b - widget.currentIndex).abs();
            return distanceB.compareTo(distanceA);
          });

        return GestureDetector(
          behavior: HitTestBehavior.translucent,
          onHorizontalDragStart: _onDragStart,
          onHorizontalDragUpdate: (details) =>
              _onDragUpdate(details, metrics.cardWidth),
          onHorizontalDragEnd: _onDragEnd,
          onHorizontalDragCancel: _onDragCancel,
          child: Stack(
            clipBehavior: Clip.none,
            children: <Widget>[
              for (final int index in visible)
                _buildItem(
                  metrics: metrics,
                  index: index,
                  dragProgress: dragProgress,
                  positionDuration: positionDuration,
                ),
            ],
          ),
        );
      },
    );
  }

  Widget _buildItem({
    required _DeckMetrics metrics,
    required int index,
    required double dragProgress,
    required Duration positionDuration,
  }) {
    final Flashcard card = widget.cards[index];
    final int relative = index - widget.currentIndex;
    final bool isActive = relative == 0;
    final bool isTowardDrag =
        _dragOffset != 0 && relative != 0 && relative.sign != _dragOffset.sign;

    final double baseScale = isActive ? 1.0 : 0.85;
    final double activeScale = baseScale - dragProgress * 0.035;
    final double neighbourScale =
        baseScale + (isTowardDrag ? dragProgress * 0.04 : 0);
    final double scale = isActive ? activeScale : neighbourScale;

    final double activeOpacity = 1.0 - dragProgress * 0.12;
    final double neighbourOpacity =
        0.35 + (isTowardDrag ? dragProgress * 0.1 : 0);
    final double opacity = isActive ? activeOpacity : neighbourOpacity;

    final double x = metrics.deckWidth / 2 -
        metrics.cardWidth / 2 +
        relative * metrics.sideOffset +
        _dragOffset * (isActive ? 1.0 : 0.2);
    final double y = (metrics.deckHeight - metrics.cardHeight) / 2;

    return AnimatedPositioned(
      key: ValueKey<String>('flashcard-position-${card.id}'),
      duration: positionDuration,
      curve: Curves.easeOutCubic,
      left: x,
      top: y,
      width: metrics.cardWidth,
      height: metrics.cardHeight,
      child: AnimatedScale(
        duration: positionDuration,
        curve: Curves.easeOutCubic,
        scale: scale,
        child: AnimatedOpacity(
          duration: positionDuration,
          curve: Curves.easeOutCubic,
          opacity: opacity,
          child: FlashcardStackItem(
            key: ValueKey<String>('flashcard-item-${card.id}'),
            card: card,
            width: metrics.cardWidth,
            height: metrics.cardHeight,
            isActive: isActive,
            flipAnimation: isActive ? widget.flipAnimation : null,
            onTap: isActive ? _handleTap : null,
          ),
        ),
      ),
    );
  }
}

class _DeckMetrics {
  const _DeckMetrics({
    required this.deckWidth,
    required this.deckHeight,
    required this.cardWidth,
    required this.cardHeight,
    required this.sideOffset,
  });

  final double deckWidth;
  final double deckHeight;
  final double cardWidth;
  final double cardHeight;
  final double sideOffset;

  factory _DeckMetrics.from(BoxConstraints constraints) {
    final double deckWidth = constraints.maxWidth;
    final double deckHeight = constraints.maxHeight;
    final double horizontalMargin = deckWidth < 500 ? 40.0 : 96.0;

    double cardWidth = math.min(deckWidth - horizontalMargin, 440.0);
    cardWidth = math.max(cardWidth, 180.0);
    double cardHeight = math.min(deckHeight * 0.94, 580.0);

    final double maxHeightForWidth = cardWidth * 1.46;
    if (cardHeight > maxHeightForWidth) cardHeight = maxHeightForWidth;

    final double maxWidthForHeight = cardHeight / 1.30;
    if (cardWidth > maxWidthForHeight) cardWidth = maxWidthForHeight;

    cardHeight = math.min(cardHeight, cardWidth * 1.46);
    cardHeight = math.max(cardHeight, 240.0);

    return _DeckMetrics(
      deckWidth: deckWidth,
      deckHeight: deckHeight,
      cardWidth: cardWidth,
      cardHeight: cardHeight,
      sideOffset: cardWidth * 0.88,
    );
  }
}
