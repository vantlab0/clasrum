import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;

import '../../core/config.dart';
import '../../core/storage.dart';
import '../../core/theme.dart';
import '../../core/widgets/glass_widgets.dart';
import '../../core/widgets/pwony_icons.dart';

/// Test Yourself screen — generate a quick AI-grounded quiz from a topic.
class TestYourselfScreen extends StatefulWidget {
  const TestYourselfScreen({
    super.key,
    required this.config,
    required this.storage,
    this.initialTopic,
    this.initialNoteContent,
  });

  final AppConfig config;
  final AppStorage storage;
  final String? initialTopic;
  final String? initialNoteContent;

  @override
  State<TestYourselfScreen> createState() => _TestYourselfScreenState();
}

class QuizQuestion {
  const QuizQuestion({
    required this.question,
    required this.options,
    required this.correctIndex,
    this.explanation,
  });

  final String question;
  final List<String> options;
  final int correctIndex;
  final String? explanation;

  factory QuizQuestion.fromJson(Map<String, dynamic> json) {
    final List<dynamic> rawOptions = json['options'] as List<dynamic>? ?? [];
    return QuizQuestion(
      question: json['question'] as String? ?? 'Untitled question',
      options: rawOptions.map((value) => '$value').toList(growable: false),
      correctIndex: (json['correct_index'] as num?)?.toInt() ?? 0,
      explanation: json['explanation'] as String?,
    );
  }
}

class _TestYourselfScreenState extends State<TestYourselfScreen> {
  static const List<int> _questionOptions = <int>[5, 10, 15, 20];
  static const List<int> _durationOptions = <int>[5, 10, 15, 20];

  late final TextEditingController _topicController;
  Timer? _timer;
  DateTime? _startedAt;

  int _questionCount = 5;
  int _durationMinutes = 5;
  int _currentIndex = 0;
  int _remainingSeconds = 5 * 60;
  bool _isGenerating = false;
  bool _timerRunning = false;
  bool _showingResults = false;
  List<QuizQuestion> _questions = const <QuizQuestion>[];
  final Map<int, int> _selectedAnswers = <int, int>{};
  String? _error;

  @override
  void initState() {
    super.initState();
    final String seed = widget.initialNoteContent != null
        ? _firstLine(widget.initialNoteContent!)
        : (widget.initialTopic?.trim() ?? '');
    _topicController = TextEditingController(text: seed);
  }

  @override
  void dispose() {
    _timer?.cancel();
    _topicController.dispose();
    super.dispose();
  }

  static String _firstLine(String value) {
    for (final String line in value.split('\n')) {
      final String trimmed = line.trim();
      if (trimmed.isNotEmpty) {
        return trimmed.length > 220 ? '${trimmed.substring(0, 220)}…' : trimmed;
      }
    }
    return '';
  }

  Map<String, String> get _authHeaders {
    final String? token = widget.storage.token;
    return <String, String>{
      'Content-Type': 'application/json',
      if (token != null) 'Authorization': 'Bearer $token',
    };
  }

  Future<String> _resolveLessonId(String topic) async {
    final String? lastLesson = widget.storage.lastLessonId;
    if (lastLesson != null && lastLesson.isNotEmpty) return lastLesson;

    final String level = widget.storage.defaultLearningLevel ?? 'high_school';
    final http.Response response = await http.post(
      widget.config.apiUri('/lessons'),
      headers: _authHeaders,
      body: jsonEncode(<String, dynamic>{
        'topic': topic,
        'learning_level': level,
        'duration_minutes': widget.storage.defaultDurationMinutes ?? 30,
      }),
    );
    if (response.statusCode != 200) {
      throw Exception('Could not create a lesson for this quiz.');
    }
    final Map<String, dynamic> body =
        jsonDecode(utf8.decode(response.bodyBytes)) as Map<String, dynamic>;
    final String id = body['id'] as String;
    await widget.storage.setLastLessonId(id);
    await widget.storage.markLessonOpened(id);
    return id;
  }

  Future<void> _generateQuiz() async {
    final String topic = _topicController.text.trim();
    if (topic.length < 2) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('Enter a topic with at least 2 characters.'),
        ),
      );
      return;
    }

    setState(() {
      _isGenerating = true;
      _error = null;
      _questions = const <QuizQuestion>[];
      _selectedAnswers.clear();
      _currentIndex = 0;
      _remainingSeconds = _durationMinutes * 60;
      _startedAt = DateTime.now();
      _timerRunning = false;
      _timer?.cancel();
      _showingResults = false;
    });

    try {
      final String lessonId = await _resolveLessonId(topic);
      final http.Response response = await http.post(
        widget.config.apiUri('/lessons/$lessonId/quiz/generate'),
        headers: _authHeaders,
        body: jsonEncode(<String, dynamic>{
          'topic': topic,
          'question_count': _questionCount,
          'duration_minutes': _durationMinutes,
          'subject': null,
          'class_level': widget.storage.defaultLearningLevel,
        }),
      );

      if (!mounted) return;
      final Map<String, dynamic> body =
          jsonDecode(utf8.decode(response.bodyBytes)) as Map<String, dynamic>;

      if (response.statusCode == 200) {
        final List<dynamic> rawQuestions =
            body['questions'] as List<dynamic>? ?? const <dynamic>[];
        final List<QuizQuestion> questions = rawQuestions
            .whereType<Map<String, dynamic>>()
            .map(QuizQuestion.fromJson)
            .toList(growable: false);

        setState(() {
          _questions = questions;
          _isGenerating = false;
          _timerRunning = questions.isNotEmpty;
        });
        if (questions.isNotEmpty) _startTimer();
        return;
      }

      final Map<String, dynamic>? error =
          body['error'] as Map<String, dynamic>?;
      setState(() {
        _isGenerating = false;
        _error = error?['message'] as String? ??
            'Quiz generation failed (HTTP ${response.statusCode}).';
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _isGenerating = false;
        _error = 'Could not generate the quiz. Please try again.';
      });
    }
  }

  void _startTimer() {
    _timer?.cancel();
    _timer = Timer.periodic(const Duration(seconds: 1), (timer) {
      if (!mounted) {
        timer.cancel();
        return;
      }
      if (!_timerRunning) return;
      if (_remainingSeconds <= 1) {
        timer.cancel();
        _submitQuiz();
        return;
      }
      setState(() => _remainingSeconds -= 1);
    });
  }

  void _submitQuiz() {
    if (_questions.isEmpty) return;
    _timer?.cancel();
    setState(() {
      _timerRunning = false;
      _showingResults = true;
    });
  }

  int get _correctCount {
    int correct = 0;
    for (int i = 0; i < _questions.length; i++) {
      if (_selectedAnswers[i] == _questions[i].correctIndex) correct++;
    }
    return correct;
  }

  String _formatClock(int seconds) {
    final int minutes = seconds ~/ 60;
    final int remainder = seconds % 60;
    return '${minutes.toString().padLeft(2, '0')}:${remainder.toString().padLeft(2, '0')}';
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.background,
      body: SafeArea(
        child: _isGenerating
            ? _buildLoadingState()
            : _showingResults
                ? _buildResultsState()
                : _questions.isNotEmpty
                    ? _buildQuizState()
                    : _buildLandingState(),
      ),
    );
  }

  Widget _buildHeader() {
    return Padding(
      padding: const EdgeInsets.fromLTRB(8, 4, 16, 4),
      child: Row(
        children: <Widget>[
          IconButton(
            tooltip: 'Back',
            onPressed: () => Navigator.of(context).maybePop(),
            icon: const PwonyIcon(PwonyIconKind.back, size: 22),
          ),
          const SizedBox(width: 8),
          const Text(
            'Test Yourself',
            style: TextStyle(
              color: AppColors.white,
              fontSize: 20,
              fontWeight: FontWeight.w600,
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildLandingState() {
    return SingleChildScrollView(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          _buildHeader(),
          const SizedBox(height: 16),
          const Text(
            'How well do you understand it?',
            textAlign: TextAlign.center,
            style: TextStyle(
              color: AppColors.white,
              fontSize: 22,
              fontWeight: FontWeight.w600,
            ),
          ),
          const SizedBox(height: 6),
          const Text(
            'Create a quick test and see what you have learned.',
            textAlign: TextAlign.center,
            style: TextStyle(
              color: AppColors.muted,
              fontSize: 14,
              height: 1.5,
            ),
          ),
          const SizedBox(height: 20),
          GlassContainer(
            radius: AppRadius.lg,
            padding: const EdgeInsets.all(16),
            blurSigma: 10,
            backgroundOpacity: 0.6,
            borderOpacity: 0.6,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                TextField(
                  controller: _topicController,
                  onSubmitted: (_) => _generateQuiz(),
                  style: const TextStyle(color: AppColors.white),
                  decoration: InputDecoration(
                    hintText: 'Enter the topic you want to practise…',
                    hintStyle: const TextStyle(color: AppColors.muted),
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
                  ),
                ),
                const SizedBox(height: 16),
                const Text(
                  'Questions',
                  style: TextStyle(
                    color: AppColors.muted,
                    fontSize: 12,
                    fontWeight: FontWeight.w600,
                  ),
                ),
                const SizedBox(height: 8),
                Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  children: _questionOptions
                      .map(
                        (count) => _Chip(
                          label: '$count',
                          selected: count == _questionCount,
                          onTap: () => setState(() => _questionCount = count),
                        ),
                      )
                      .toList(),
                ),
                const SizedBox(height: 12),
                const Text(
                  'Duration',
                  style: TextStyle(
                    color: AppColors.muted,
                    fontSize: 12,
                    fontWeight: FontWeight.w600,
                  ),
                ),
                const SizedBox(height: 8),
                Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  children: _durationOptions
                      .map(
                        (minutes) => _Chip(
                          label: '$minutes min',
                          selected: minutes == _durationMinutes,
                          onTap: () => setState(() {
                            _durationMinutes = minutes;
                            _remainingSeconds = minutes * 60;
                          }),
                        ),
                      )
                      .toList(),
                ),
              ],
            ),
          ),
          if (_error != null) ...<Widget>[
            const SizedBox(height: 12),
            Text(
              _error!,
              textAlign: TextAlign.center,
              style: const TextStyle(color: AppColors.red, fontSize: 13),
            ),
          ],
          const SizedBox(height: 16),
          ElevatedButton(
            onPressed: _isGenerating ? null : _generateQuiz,
            style: ElevatedButton.styleFrom(
              backgroundColor: AppColors.blue,
              foregroundColor: AppColors.white,
              minimumSize: const Size(0, 48),
              shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(AppRadius.md),
              ),
            ),
            child: const Text(
              'Generate Test',
              style: TextStyle(fontWeight: FontWeight.w600),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildLoadingState() {
    return Center(
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: const <Widget>[
          CircularProgressIndicator(strokeWidth: 2),
          SizedBox(height: 16),
          Text(
            'Creating your quiz…',
            style: TextStyle(color: AppColors.muted, fontSize: 14),
          ),
        ],
      ),
    );
  }

  Widget _buildQuizState() {
    final QuizQuestion question = _questions[_currentIndex];
    final int? selected = _selectedAnswers[_currentIndex];
    final int progress = _questions.length;

    return Column(
      children: <Widget>[
        _buildHeader(),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
          child: Row(
            children: <Widget>[
              Text(
                'Question ${_currentIndex + 1}/$progress',
                style: const TextStyle(
                  color: AppColors.muted,
                  fontSize: 12,
                ),
              ),
              const Spacer(),
              Text(
                _formatClock(_remainingSeconds),
                style: const TextStyle(
                  color: AppColors.white,
                  fontSize: 13,
                  fontWeight: FontWeight.w600,
                ),
              ),
            ],
          ),
        ),
        Expanded(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: <Widget>[
                Text(
                  question.question,
                  style: const TextStyle(
                    color: AppColors.white,
                    fontSize: 21,
                    fontWeight: FontWeight.w600,
                    height: 1.35,
                  ),
                ),
                const SizedBox(height: 20),
                for (int i = 0; i < question.options.length; i++) ...<Widget>[
                  _AnswerOption(
                    label: question.options[i],
                    selected: selected == i,
                    onTap: () {
                      setState(() => _selectedAnswers[_currentIndex] = i);
                    },
                  ),
                  const SizedBox(height: 10),
                ],
                const SizedBox(height: 12),
                Row(
                  children: <Widget>[
                    if (_currentIndex > 0)
                      TextButton(
                        onPressed: () => setState(() => _currentIndex -= 1),
                        child: const Text('Previous'),
                      ),
                    const Spacer(),
                    ElevatedButton(
                      onPressed: _currentIndex >= _questions.length - 1
                          ? _submitQuiz
                          : () => setState(() => _currentIndex += 1),
                      style: ElevatedButton.styleFrom(
                        backgroundColor: AppColors.blue,
                        foregroundColor: AppColors.white,
                      ),
                      child: Text(
                        _currentIndex >= _questions.length - 1
                            ? 'Submit'
                            : 'Next',
                      ),
                    ),
                  ],
                ),
              ],
            ),
          ),
        ),
      ],
    );
  }

  Widget _buildResultsState() {
    final int total = _questions.length;
    final int correct = _correctCount;
    final int percentage = total == 0 ? 0 : (correct * 100 / total).round();
    final Duration elapsed = _startedAt == null
        ? Duration.zero
        : DateTime.now().difference(_startedAt!);

    return Center(
      child: SingleChildScrollView(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            const PwonyIcon(
              PwonyIconKind.startLearning,
              size: 52,
              color: AppColors.white,
            ),
            const SizedBox(height: 16),
            Text(
              '$correct / $total correct',
              style: const TextStyle(
                color: AppColors.white,
                fontSize: 24,
                fontWeight: FontWeight.w600,
              ),
            ),
            const SizedBox(height: 6),
            Text(
              '$percentage% • ${elapsed.inMinutes}:${(elapsed.inSeconds % 60).toString().padLeft(2, '0')}',
              style: const TextStyle(color: AppColors.muted, fontSize: 14),
            ),
            const SizedBox(height: 24),
            ElevatedButton(
              onPressed: () => setState(() {
                _questions = const <QuizQuestion>[];
                _selectedAnswers.clear();
                _showingResults = false;
                _timerRunning = false;
                _remainingSeconds = _durationMinutes * 60;
              }),
              style: ElevatedButton.styleFrom(
                backgroundColor: AppColors.blue,
                foregroundColor: AppColors.white,
                minimumSize: const Size(0, 46),
              ),
              child: const Text('Try another topic'),
            ),
          ],
        ),
      ),
    );
  }
}

class _Chip extends StatelessWidget {
  const _Chip({
    required this.label,
    required this.selected,
    required this.onTap,
  });

  final String label;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: Colors.transparent,
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(999),
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
          decoration: BoxDecoration(
            color: selected
                ? AppColors.blue.withValues(alpha: 0.22)
                : AppColors.surface.withValues(alpha: 0.7),
            borderRadius: BorderRadius.circular(999),
            border: Border.all(
              color: selected
                  ? AppColors.blue.withValues(alpha: 0.9)
                  : AppColors.border.withValues(alpha: 0.7),
            ),
          ),
          child: Text(
            label,
            style: TextStyle(
              color: selected ? AppColors.white : AppColors.muted,
              fontSize: 12,
              fontWeight: FontWeight.w600,
            ),
          ),
        ),
      ),
    );
  }
}

class _AnswerOption extends StatelessWidget {
  const _AnswerOption({
    required this.label,
    required this.selected,
    required this.onTap,
  });

  final String label;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: Colors.transparent,
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(AppRadius.md),
        child: Container(
          width: double.infinity,
          padding: const EdgeInsets.all(14),
          decoration: BoxDecoration(
            color: selected
                ? AppColors.blue.withValues(alpha: 0.22)
                : AppColors.surface.withValues(alpha: 0.7),
            borderRadius: BorderRadius.circular(AppRadius.md),
            border: Border.all(
              color: selected
                  ? AppColors.blue.withValues(alpha: 0.9)
                  : AppColors.border.withValues(alpha: 0.7),
            ),
          ),
          child: Text(
            label,
            style: TextStyle(
              color: AppColors.white,
              fontSize: 14,
              height: 1.4,
              fontWeight: selected ? FontWeight.w600 : FontWeight.w400,
            ),
          ),
        ),
      ),
    );
  }
}
