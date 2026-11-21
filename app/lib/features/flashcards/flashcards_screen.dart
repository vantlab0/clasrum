import 'package:flutter/material.dart';

import '../../core/config.dart';
import '../../core/storage.dart';
import '../../core/theme.dart';
import '../../core/widgets/glass_widgets.dart';
import '../../core/widgets/pwony_icons.dart';
import 'flashcard_study_screen.dart';
import 'services/flashcard_service.dart';

/// Flashcards landing screen — students enter a topic and generate flashcards.
class FlashcardsScreen extends StatefulWidget {
  const FlashcardsScreen({
    super.key,
    required this.config,
    required this.storage,
    this.initialNoteTitle,
    this.initialNoteContent,
    this.initialLessonId,
    this.initialSubject,
    this.initialClassLevel,
  });

  final AppConfig config;
  final AppStorage storage;
  final String? initialNoteTitle;
  final String? initialNoteContent;
  final String? initialLessonId;
  final String? initialSubject;
  final String? initialClassLevel;

  @override
  State<FlashcardsScreen> createState() => _FlashcardsScreenState();
}

class _FlashcardsScreenState extends State<FlashcardsScreen> {
  late final TextEditingController _topicController;
  late String _topic;
  bool _isGenerating = false;
  String? _subject;
  String? _classLevel;
  String? _lessonId;

  @override
  void initState() {
    super.initState();
    final String seed = widget.initialNoteTitle?.trim().isNotEmpty == true
        ? widget.initialNoteTitle!.trim()
        : _firstLine(widget.initialNoteContent);
    _topic = seed;
    _topicController = TextEditingController(text: seed);
    _subject = widget.initialSubject;
    _classLevel =
        widget.initialClassLevel ?? widget.storage.defaultLearningLevel;
    _lessonId = widget.initialLessonId;
  }

  @override
  void dispose() {
    _topicController.dispose();
    super.dispose();
  }

  static String _firstLine(String? value) {
    if (value == null) return '';
    for (final String line in value.split('\n')) {
      final String trimmed = line.trim();
      if (trimmed.isNotEmpty) {
        return trimmed.length > 180
            ? '${trimmed.substring(0, 180)}...'
            : trimmed;
      }
    }
    return '';
  }

  Future<String> _resolveLessonId(
    FlashcardService service,
    String topic,
  ) async {
    final String? existing = _lessonId;
    if (existing != null && existing.isNotEmpty) return existing;

    final String? lastLesson = widget.storage.lastLessonId;
    if (lastLesson != null && lastLesson.isNotEmpty) {
      _lessonId = lastLesson;
      return lastLesson;
    }

    final String created = await service.createLesson(
      topic: topic,
      learningLevel: _classLevel ?? 'high_school',
      durationMinutes: widget.storage.defaultDurationMinutes ?? 30,
      subject: _subject,
    );
    _lessonId = created;
    await widget.storage.setLastLessonId(created);
    await widget.storage.markLessonOpened(created);
    return created;
  }

  Future<void> _generateFlashcards() async {
    final String topic = _topicController.text.trim();
    if (topic.length < 2) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text('Enter a topic with at least 2 characters.'),
          ),
        );
      }
      return;
    }

    setState(() => _isGenerating = true);
    final FlashcardService service = FlashcardService(
      config: widget.config,
      storage: widget.storage,
    );

    try {
      final String lessonId = await _resolveLessonId(service, topic);
      final FlashcardSetSummary summary = await service.generate(
        lessonId: lessonId,
        topic: topic,
        subject: _subject,
        classLevel: _classLevel,
      );
      if (!mounted) return;

      await Navigator.of(context).push(
        MaterialPageRoute<void>(
          builder: (_) => FlashcardStudyScreen(
            config: widget.config,
            storage: widget.storage,
            lessonId: lessonId,
            flashcardSetId: summary.id,
            topic: summary.topic,
            subject: summary.subject ?? _subject,
            classLevel: summary.classLevel ?? _classLevel,
          ),
        ),
      );
    } on FlashcardServiceException catch (error) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(error.message)),
      );
    } catch (_) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('Could not generate flashcards. Please try again.'),
        ),
      );
    } finally {
      service.close();
      if (mounted) setState(() => _isGenerating = false);
    }
  }

  Widget _buildSubjectSelector() {
    return GlassContainer(
      radius: AppRadius.md,
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 9),
      blurSigma: 8,
      backgroundOpacity: 0.55,
      borderOpacity: 0.5,
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          const PwonyIcon(
            PwonyIconKind.profile,
            size: 15,
            color: AppColors.muted,
          ),
          const SizedBox(width: 6),
          Text(
            _subject?.trim().isNotEmpty == true ? _subject! : 'General',
            style: const TextStyle(
              color: AppColors.white,
              fontSize: 12,
              fontWeight: FontWeight.w500,
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildClassLevelSelector() {
    final String existingLevel =
        _classLevel ?? widget.storage.defaultLearningLevel ?? 'high_school';
    return GlassContainer(
      radius: AppRadius.md,
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 9),
      blurSigma: 8,
      backgroundOpacity: 0.55,
      borderOpacity: 0.5,
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          const PwonyIcon(
            PwonyIconKind.notes,
            size: 15,
            color: AppColors.muted,
          ),
          const SizedBox(width: 6),
          Text(
            existingLevel.replaceAll('_', ' '),
            style: const TextStyle(
              color: AppColors.white,
              fontSize: 12,
              fontWeight: FontWeight.w500,
            ),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final bool isEmpty = _topic.trim().isEmpty;

    return Scaffold(
      backgroundColor: AppColors.background,
      body: SafeArea(
        top: true,
        bottom: false,
        child: SingleChildScrollView(
          padding: const EdgeInsets.only(
            top: AppSpacing.xl,
            left: AppSpacing.lg,
            right: AppSpacing.lg,
            bottom: AppSpacing.lg,
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              _buildHeader(context),
              const SizedBox(height: AppSpacing.xl),
              const Text(
                'Learn through active recall.',
                style: TextStyle(
                  color: AppColors.white,
                  fontSize: 20,
                  fontWeight: FontWeight.w600,
                  height: 1.2,
                ),
              ),
              const SizedBox(height: AppSpacing.xs),
              Text(
                'Turn any topic into interactive flashcards that help you remember.',
                style: TextStyle(
                  color: AppColors.muted,
                  fontSize: 14,
                  height: 1.5,
                ),
              ),
              const SizedBox(height: AppSpacing.lg),
              GlassContainer(
                radius: AppRadius.lg,
                padding: const EdgeInsets.fromLTRB(16, 20, 16, 20),
                blurSigma: 10,
                backgroundOpacity: 0.6,
                borderOpacity: 0.6,
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    TextField(
                      controller: _topicController,
                      onChanged: (value) => setState(() => _topic = value),
                      onSubmitted: (_) => _generateFlashcards(),
                      style: const TextStyle(color: AppColors.white),
                      decoration: InputDecoration(
                        hintText: 'Enter a topic you want to study...',
                        hintStyle: TextStyle(
                          color: AppColors.muted.withValues(alpha: 0.9),
                        ),
                        filled: true,
                        fillColor: AppColors.surface.withValues(alpha: 0.7),
                        border: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(AppRadius.lg),
                          borderSide: BorderSide(
                            color: AppColors.border.withValues(alpha: 0.7),
                          ),
                        ),
                        enabledBorder: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(AppRadius.lg),
                          borderSide: BorderSide(
                            color: AppColors.border.withValues(alpha: 0.7),
                          ),
                        ),
                        focusedBorder: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(AppRadius.lg),
                          borderSide: const BorderSide(
                            color: AppColors.blue,
                            width: 1.4,
                          ),
                        ),
                        contentPadding: const EdgeInsets.symmetric(
                          horizontal: 18,
                          vertical: 14,
                        ),
                        isDense: true,
                      ),
                      maxLines: 1,
                      cursorColor: AppColors.blue,
                    ),
                    if (_topic.isNotEmpty) ...<Widget>[
                      const SizedBox(height: AppSpacing.sm),
                      Wrap(
                        spacing: 8,
                        runSpacing: 8,
                        children: <Widget>[
                          _buildSubjectSelector(),
                          _buildClassLevelSelector(),
                        ],
                      ),
                    ],
                  ],
                ),
              ),
              const SizedBox(height: AppSpacing.lg),
              SizedBox(
                width: double.infinity,
                child: GlassContainer(
                  radius: AppRadius.md,
                  padding: const EdgeInsets.symmetric(
                    horizontal: 20,
                    vertical: 14,
                  ),
                  blurSigma: 8,
                  backgroundOpacity: 0.7,
                  borderOpacity: 0.5,
                  child: ElevatedButton(
                    onPressed:
                        _isGenerating || isEmpty ? null : _generateFlashcards,
                    style: ElevatedButton.styleFrom(
                      backgroundColor: AppColors.blue,
                      foregroundColor: AppColors.white,
                      minimumSize: const Size(0, 44),
                      padding: const EdgeInsets.symmetric(
                        horizontal: 20,
                        vertical: 12,
                      ),
                      shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(AppRadius.md),
                      ),
                      elevation: 0,
                    ),
                    child: _isGenerating
                        ? const Row(
                            mainAxisSize: MainAxisSize.min,
                            children: <Widget>[
                              SizedBox(
                                width: 16,
                                height: 16,
                                child: CircularProgressIndicator(
                                  strokeWidth: 2,
                                  color: AppColors.white,
                                ),
                              ),
                              SizedBox(width: 8),
                              Text(
                                'Generating flashcards',
                                style: TextStyle(
                                  fontSize: 14,
                                  fontWeight: FontWeight.w600,
                                ),
                              ),
                            ],
                          )
                        : const Row(
                            mainAxisSize: MainAxisSize.min,
                            children: <Widget>[
                              Text(
                                'Generate Flashcards',
                                style: TextStyle(
                                  fontSize: 14,
                                  fontWeight: FontWeight.w600,
                                ),
                              ),
                              SizedBox(width: 4),
                              PwonyIcon(
                                PwonyIconKind.startLearning,
                                size: 16,
                                color: AppColors.white,
                              ),
                            ],
                          ),
                  ),
                ),
              ),
              if (_isGenerating) ...<Widget>[
                const SizedBox(height: AppSpacing.md),
                const Center(
                  child: Text(
                    'Creating your flashcards...',
                    style: TextStyle(color: AppColors.muted, fontSize: 14),
                  ),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildHeader(BuildContext context) {
    return Row(
      children: <Widget>[
        IconButton(
          icon: const PwonyIcon(PwonyIconKind.back, size: 22),
          tooltip: 'Back',
          onPressed: () => Navigator.of(context).maybePop(),
        ),
        const SizedBox(width: 8),
        const Text(
          'Flashcards',
          style: TextStyle(
            color: AppColors.white,
            fontSize: 20,
            fontWeight: FontWeight.w600,
          ),
        ),
      ],
    );
  }
}
