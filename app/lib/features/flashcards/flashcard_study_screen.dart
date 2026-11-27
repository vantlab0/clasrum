import 'package:flutter/material.dart';

import '../../core/config.dart';
import '../../core/storage.dart';
import '../../core/theme.dart';
import 'models/flashcard.dart';
import 'services/flashcard_service.dart';
import 'widgets/flashcard_deck.dart';
import 'widgets/flashcard_progress_indicator.dart';

export 'models/flashcard.dart'
    show Flashcard, FlashcardSet, FlashcardSetSummary;

/// Immersive flashcard study experience.
///
/// The screen accepts either already-loaded [initialCards] (used by tests and
/// callers that already hold a deck) or a persisted [flashcardSetId] that is
/// fetched and polled from the existing AI flashcard backend.
class FlashcardStudyScreen extends StatefulWidget {
  const FlashcardStudyScreen({
    super.key,
    this.config,
    this.storage,
    this.lessonId,
    this.flashcardSetId,
    this.topic = '',
    this.subject,
    this.classLevel,
    this.initialCards = const <Flashcard>[],
    this.initialIndex = 0,
  });

  final AppConfig? config;
  final AppStorage? storage;
  final String? lessonId;
  final String? flashcardSetId;
  final String topic;
  final String? subject;
  final String? classLevel;
  final List<Flashcard> initialCards;
  final int initialIndex;

  @override
  State<FlashcardStudyScreen> createState() => _FlashcardStudyScreenState();
}

class _FlashcardStudyScreenState extends State<FlashcardStudyScreen>
    with SingleTickerProviderStateMixin {
  static const Color _backgroundColor = AppColors.background;

  late final AnimationController _flipController;
  List<Flashcard> _cards = const <Flashcard>[];
  int _currentIndex = 0;
  bool _loading = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _flipController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 650),
    );
    _flipController.addStatusListener(_onFlipStatusChanged);

    if (widget.initialCards.isNotEmpty) {
      _cards = _sorted(widget.initialCards);
      final int maxIndex = _cards.length - 1;
      _currentIndex = widget.initialIndex < 0
          ? 0
          : (widget.initialIndex > maxIndex ? maxIndex : widget.initialIndex);
    } else if (widget.flashcardSetId != null) {
      _loadSet();
    }
  }

  @override
  void dispose() {
    _flipController
      ..removeStatusListener(_onFlipStatusChanged)
      ..dispose();
    super.dispose();
  }

  void _onFlipStatusChanged(AnimationStatus status) {
    if (status == AnimationStatus.completed) {
      setState(() {});
    }
  }

  List<Flashcard> _sorted(List<Flashcard> cards) {
    final List<Flashcard> sorted = List<Flashcard>.of(cards);
    sorted.sort((a, b) => a.order.compareTo(b.order));
    return List<Flashcard>.unmodifiable(sorted);
  }

  Future<void> _loadSet() async {
    final AppConfig? config = widget.config;
    final AppStorage? storage = widget.storage;
    final String? setId = widget.flashcardSetId;
    final String? lessonId = widget.lessonId ?? storage?.lastLessonId;

    if (config == null || storage == null || setId == null) {
      setState(() {
        _error = 'Flashcard data is unavailable.';
      });
      return;
    }
    if (lessonId == null || lessonId.isEmpty) {
      setState(() {
        _error = 'This flashcard set is not linked to a lesson.';
      });
      return;
    }

    setState(() {
      _loading = true;
      _error = null;
    });

    final FlashcardService service = FlashcardService(
      config: config,
      storage: storage,
    );
    try {
      final FlashcardSet set = await service.waitUntilReady(
        lessonId: lessonId,
        setId: setId,
      );
      if (!mounted) return;
      setState(() {
        _cards = _sorted(set.cards);
        _currentIndex = 0;
        _loading = false;
      });
    } on FlashcardServiceException catch (error) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = error.message;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = 'Unable to load this flashcard deck. Please try again.';
      });
    } finally {
      service.close();
    }
  }

  void _navigateTo(int index) {
    if (index < 0 || index >= _cards.length || index == _currentIndex) return;
    _flipController
      ..stop()
      ..value = 0;
    setState(() => _currentIndex = index);
  }

  void _flipActiveCard() {
    if (_cards.isEmpty || _loading || _error != null) return;
    if (_flipController.isAnimating) return;
    if (_flipController.value >= 0.5) {
      _flipController.reverse();
    } else {
      _flipController.forward();
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: _backgroundColor,
      body: Stack(
        fit: StackFit.expand,
        children: <Widget>[
          SafeArea(
            child: AnimatedSwitcher(
              duration: const Duration(milliseconds: 260),
              switchInCurve: Curves.easeOut,
              switchOutCurve: Curves.easeIn,
              child: _buildBody(),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildBody() {
    if (_loading) {
      return const _StatusPanel(
        key: ValueKey<String>('flashcards-loading'),
        message: 'Preparing your flashcards...',
        loading: true,
      );
    }

    if (_error != null) {
      return _StatusPanel(
        key: const ValueKey<String>('flashcards-error'),
        message: _error!,
        actionLabel: widget.flashcardSetId != null ? 'Try again' : null,
        onAction: widget.flashcardSetId != null ? _loadSet : null,
        secondaryLabel: 'Go back',
        onSecondary: () => Navigator.of(context).maybePop(),
      );
    }

    if (_cards.isEmpty) {
      return _StatusPanel(
        key: const ValueKey<String>('flashcards-empty'),
        message: 'This deck does not contain any flashcards yet.',
        secondaryLabel: 'Go back',
        onSecondary: () => Navigator.of(context).maybePop(),
      );
    }

    final int total = _cards.length;
    final double progress = (_currentIndex + 1) / total;

    return Column(
      key: const ValueKey<String>('flashcards-content'),
      children: <Widget>[
        const SizedBox(height: 10),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 24),
          child: FlashcardProgressIndicator(
            progress: progress,
            currentIndex: _currentIndex,
            total: total,
          ),
        ),
        const SizedBox(height: 14),
        Expanded(
          child: Padding(
            padding: const EdgeInsets.only(bottom: 10),
            child: FlashcardDeck(
              cards: _cards,
              currentIndex: _currentIndex,
              flipAnimation: _flipController,
              onFlip: _flipActiveCard,
              onNavigate: _navigateTo,
            ),
          ),
        ),
      ],
    );
  }
}

class _StatusPanel extends StatelessWidget {
  const _StatusPanel({
    super.key,
    required this.message,
    this.loading = false,
    this.actionLabel,
    this.onAction,
    this.secondaryLabel,
    this.onSecondary,
  });

  final String message;
  final bool loading;
  final String? actionLabel;
  final VoidCallback? onAction;
  final String? secondaryLabel;
  final VoidCallback? onSecondary;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 340),
        child: Padding(
          padding: const EdgeInsets.all(28),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: <Widget>[
              if (loading) ...<Widget>[
                SizedBox(
                  width: 20,
                  height: 20,
                  child: CircularProgressIndicator(
                    strokeWidth: 1.6,
                    color: Colors.white.withValues(alpha: 0.78),
                  ),
                ),
                const SizedBox(height: 20),
              ],
              Text(
                message,
                textAlign: TextAlign.center,
                style: TextStyle(
                  color: Colors.white.withValues(alpha: 0.76),
                  fontSize: 15,
                  height: 1.55,
                  fontWeight: FontWeight.w300,
                ),
              ),
              if (actionLabel != null || secondaryLabel != null) ...<Widget>[
                const SizedBox(height: 24),
                Row(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: <Widget>[
                    if (actionLabel != null)
                      _QuietButton(
                        label: actionLabel!,
                        onPressed: onAction,
                      ),
                    if (actionLabel != null && secondaryLabel != null)
                      const SizedBox(width: 8),
                    if (secondaryLabel != null)
                      _QuietButton(
                        label: secondaryLabel!,
                        onPressed: onSecondary,
                      ),
                  ],
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

class _QuietButton extends StatelessWidget {
  const _QuietButton({required this.label, required this.onPressed});

  final String label;
  final VoidCallback? onPressed;

  @override
  Widget build(BuildContext context) {
    return TextButton(
      onPressed: onPressed,
      style: TextButton.styleFrom(
        foregroundColor: Colors.white.withValues(alpha: 0.9),
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(999),
          side: BorderSide(color: Colors.white.withValues(alpha: 0.12)),
        ),
      ),
      child: Text(
        label,
        style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w400),
      ),
    );
  }
}
