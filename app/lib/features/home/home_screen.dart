import 'dart:convert' as convert;

import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;

import '../../core/config.dart';
import '../../core/errors.dart';
import '../../core/formatters.dart';
import '../../core/storage.dart';
import '../../core/theme.dart';
import '../../core/widgets/clasrum_icons.dart';
import '../../core/widgets/glass_widgets.dart';
import '../../core/widgets/profile_menu.dart';
import '../../core/widgets/progress_track.dart';
import '../../main.dart';
import '../../services/realtime_client.dart';

/// Home screen: premium entry point to start or continue learning.
class HomeScreen extends StatefulWidget {
  const HomeScreen({
    super.key,
    required this.config,
    required this.storage,
    required this.onStartLesson,
  });

  final AppConfig config;
  final AppStorage storage;
  final void Function(String lessonId) onStartLesson;

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  final _topicController = TextEditingController();
  late String _level;
  late int _duration;
  bool _creating = false;
  String? _error;

  List<Map<String, dynamic>> _lessons = const [];
  bool _loadingLessons = true;
  String? _displayName;

  @override
  void initState() {
    super.initState();
    _level = widget.storage.defaultLearningLevel ?? 'beginner';
    _duration = widget.storage.defaultDurationMinutes ?? 30;
    _loadLessons();
    _loadProfileName();
  }

  @override
  void dispose() {
    _topicController.dispose();
    super.dispose();
  }

  Map<String, String> get _authHeaders {
    final token = widget.storage.token;
    return <String, String>{
      if (token != null) 'Authorization': 'Bearer $token',
      'Content-Type': 'application/json',
    };
  }

  Future<void> _handleAuthFailure() async {
    try {
      final realtime = RealtimeClientScope.maybeOf(context);
      if (realtime != null) await realtime.disconnect();
    } catch (_) {}
    if (!mounted) return;
    final auth = AuthScope.maybeOf(context)?.authService;
    if (auth != null) await auth.signOutExpiredSession();
  }

  Future<void> _loadProfileName() async {
    try {
      final token = widget.storage.token;
      if (token == null) return;
      final response = await http.get(
        widget.config.apiUri('/me'),
        headers: _authHeaders,
      );
      if (response.statusCode == 200) {
        final data = convert.jsonDecode(response.body) as Map<String, dynamic>;
        if (mounted) {
          setState(() => _displayName = data['display_name'] as String?);
        }
      } else if (response.statusCode == 401 || response.statusCode == 403) {
        await _handleAuthFailure();
      }
    } catch (_) {
      // Non-critical; greeting falls back gracefully.
    }
  }

  Future<void> _loadLessons() async {
    try {
      final token = widget.storage.token;
      if (token == null) {
        if (mounted) setState(() => _loadingLessons = false);
        return;
      }
      final response = await http.get(
        widget.config.apiUri('/lessons'),
        headers: _authHeaders,
      );
      if (!mounted) return;
      if (response.statusCode == 200) {
        final data = convert.jsonDecode(response.body) as Map<String, dynamic>;
        final items = (data['items'] as List? ?? const <dynamic>[])
            .cast<Map<String, dynamic>>();
        setState(() {
          _lessons = items;
          _loadingLessons = false;
        });
      } else if (response.statusCode == 401 || response.statusCode == 403) {
        await _handleAuthFailure();
        if (mounted) {
          setState(() {
            _lessons = const [];
            _loadingLessons = false;
          });
        }
      } else {
        setState(() => _loadingLessons = false);
      }
    } catch (_) {
      if (mounted) setState(() => _loadingLessons = false);
    }
  }

  Future<void> _createLesson() async {
    final topic = _topicController.text.trim();
    if (topic.length < 2) {
      setState(() => _error = 'Enter a topic to start learning.');
      return;
    }
    setState(() {
      _creating = true;
      _error = null;
    });
    try {
      final token = widget.storage.token;
      if (token == null) {
        await _handleAuthFailure();
        return;
      }
      await widget.storage.setDefaultLearningLevel(_level);
      await widget.storage.setDefaultDurationMinutes(_duration);
      final response = await http.post(
        widget.config.apiUri('/lessons'),
        headers: _authHeaders,
        body: convert.jsonEncode(<String, dynamic>{
          'topic': topic,
          'learning_level': _level,
          'duration_minutes': _duration,
        }),
      );
      if (response.statusCode == 200) {
        final data = convert.jsonDecode(response.body) as Map<String, dynamic>;
        final id = data['id'] as String;
        await widget.storage.setLastLessonId(id);
        widget.onStartLesson(id);
      } else {
        Map<String, dynamic> body = const <String, dynamic>{};
        try {
          final decoded = convert.jsonDecode(response.body);
          if (decoded is Map<String, dynamic>) body = decoded;
        } catch (_) {}
        final exc = ApiException.fromEnvelope(
          statusCode: response.statusCode,
          body: body,
        );
        if (exc.isAuthFailure) {
          await _handleAuthFailure();
          return;
        }
        setState(() => _error = exc.friendlyMessage);
      }
    } catch (e) {
      if (e is ApiException && e.isAuthFailure) {
        await _handleAuthFailure();
      } else {
        setState(() => _error = friendlyErrorMessage(e));
      }
    } finally {
      if (mounted) setState(() => _creating = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final bottomPad = 110 + MediaQuery.paddingOf(context).bottom;
    return Scaffold(
      backgroundColor: AppColors.background,
      body: SafeArea(
        bottom: false,
        child: RefreshIndicator(
          color: AppColors.blue,
          onRefresh: _loadLessons,
          child: ListView(
            padding: EdgeInsets.fromLTRB(20, 16, 20, bottomPad),
            children: <Widget>[
              _buildHeader(context),
              const SizedBox(height: 28),
              Text(
                'What do you want to learn today?',
                style: Theme.of(context).textTheme.headlineSmall?.copyWith(
                      fontWeight: FontWeight.w600,
                      height: 1.25,
                    ),
              ),
              const SizedBox(height: 14),
              _buildTopicCard(context),
              if (_error != null) ...[
                const SizedBox(height: 12),
                Text(
                  _error!,
                  style: const TextStyle(color: AppColors.red, fontSize: 13),
                ),
              ],
              const SizedBox(height: 32),
              Text(
                'Recent sessions',
                style: Theme.of(context).textTheme.titleMedium,
              ),
              const SizedBox(height: 12),
              _buildRecentSessions(),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildHeader(BuildContext context) {
    final name = _displayName?.trim();
    final greeting = (name != null && name.isNotEmpty) ? 'Hello, $name' : 'Hello';
    return Row(
      children: <Widget>[
        Expanded(
          child: Text(
            greeting,
            style: Theme.of(context).textTheme.titleLarge?.copyWith(
                  fontWeight: FontWeight.w600,
                ),
          ),
        ),
        ProfileMenuButton(
          config: widget.config,
          storage: widget.storage,
          size: 36,
        ),
      ],
    );
  }

  Widget _buildTopicCard(BuildContext context) {
    return GlassContainer(
      radius: 22,
      padding: const EdgeInsets.fromLTRB(16, 14, 16, 12),
      blurSigma: 12,
      backgroundOpacity: 0.42,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          TextField(
            controller: _topicController,
            minLines: 2,
            maxLines: 4,
            maxLength: 500,
            style: const TextStyle(color: AppColors.white, height: 1.4),
            decoration: InputDecoration(
              hintText: 'e.g. Introduction to Differentiation',
              hintStyle: TextStyle(
                color: AppColors.muted.withValues(alpha: 0.9),
              ),
              filled: false,
              border: InputBorder.none,
              enabledBorder: InputBorder.none,
              focusedBorder: InputBorder.none,
              contentPadding: EdgeInsets.zero,
              counterText: '',
              isDense: true,
            ),
          ),
          const SizedBox(height: 10),
          Row(
            crossAxisAlignment: CrossAxisAlignment.center,
            children: <Widget>[
              Expanded(
                child: Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  children: <Widget>[
                    _compactSelector<int>(
                      value: _duration,
                      items: supportedDurations,
                      labelOf: durationLabel,
                      onChanged: (v) {
                        if (v != null) setState(() => _duration = v);
                      },
                    ),
                    _compactSelector<String>(
                      value: _level,
                      items: learningLevels,
                      labelOf: levelLabel,
                      onChanged: (v) {
                        if (v != null) setState(() => _level = v);
                      },
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 12),
              _StartButton(
                loading: _creating,
                onPressed: _creating ? null : _createLesson,
              ),
            ],
          ),
        ],
      ),
    );
  }

  Widget _compactSelector<T>({
    required T value,
    required List<T> items,
    required String Function(T) labelOf,
    required ValueChanged<T?> onChanged,
  }) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10),
      decoration: BoxDecoration(
        color: AppColors.surface.withValues(alpha: 0.35),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: AppColors.border.withValues(alpha: 0.45)),
      ),
      child: DropdownButtonHideUnderline(
        child: DropdownButton<T>(
          value: value,
          isDense: true,
          dropdownColor: AppColors.surface,
          icon: const ClasrumIcon(ClasrumIconKind.chevronDown,
              size: 16, color: AppColors.muted),
          style: const TextStyle(
            color: AppColors.muted,
            fontSize: 12,
            fontWeight: FontWeight.w500,
          ),
          items: items
              .map((item) => DropdownMenuItem<T>(
                    value: item,
                    child: Text(labelOf(item)),
                  ))
              .toList(),
          onChanged: onChanged,
        ),
      ),
    );
  }

  Widget _buildRecentSessions() {
    if (_loadingLessons) {
      return const Padding(
        padding: EdgeInsets.symmetric(vertical: 24),
        child: Center(
          child: SizedBox(
            width: 22,
            height: 22,
            child: CircularProgressIndicator(strokeWidth: 2),
          ),
        ),
      );
    }
    if (_lessons.isEmpty) {
      return Padding(
        padding: const EdgeInsets.only(top: 8),
        child: GlassContainer(
          radius: 18,
          padding: const EdgeInsets.all(20),
          blurSigma: 8,
          backgroundOpacity: 0.3,
          child: const Text(
            'No recent sessions yet. Start your first lesson above.',
            textAlign: TextAlign.center,
            style: TextStyle(color: AppColors.muted, fontSize: 14, height: 1.5),
          ),
        ),
      );
    }
    return Column(
      children: _lessons.take(8).map((lesson) {
        final id = lesson['id'] as String? ?? '';
        final topic = lesson['topic'] as String? ?? 'Untitled lesson';
        final progress = (lesson['progress'] as num?)?.toDouble() ?? 0.0;
        final updatedAt = _parseDate(lesson['updated_at'] ?? lesson['created_at']);
        final status = lesson['status'] as String? ?? '';
        final subtitle = <String>[
          if (updatedAt != null) relativeTime(updatedAt),
          if (status.isNotEmpty) lessonStatusLabel(status),
        ].join(' · ');
        return Padding(
          padding: const EdgeInsets.only(bottom: 10),
          child: GlassContainer(
            radius: 18,
            padding: EdgeInsets.zero,
            blurSigma: 8,
            backgroundOpacity: 0.32,
            child: Material(
              type: MaterialType.transparency,
              child: InkWell(
                borderRadius: BorderRadius.circular(18),
                onTap: id.isEmpty
                    ? null
                    : () {
                        widget.storage.setLastLessonId(id);
                        widget.onStartLesson(id);
                      },
                child: Padding(
                  padding: const EdgeInsets.fromLTRB(14, 12, 12, 12),
                  child: Row(
                    children: <Widget>[
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: <Widget>[
                            Text(
                              topic,
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: const TextStyle(
                                color: AppColors.white,
                                fontSize: 15,
                                fontWeight: FontWeight.w600,
                              ),
                            ),
                            const SizedBox(height: 4),
                            Text(
                              subtitle.isEmpty ? '—' : subtitle,
                              style: const TextStyle(
                                color: AppColors.muted,
                                fontSize: 12,
                              ),
                            ),
                            if (progress > 0) ...[
                              const SizedBox(height: 8),
                              ProgressTrack(value: progress, height: 4),
                            ],
                          ],
                        ),
                      ),
                      const SizedBox(width: 8),
                      const ClasrumIcon(ClasrumIconKind.chevronRight,
                          size: 14, color: AppColors.muted),
                    ],
                  ),
                ),
              ),
            ),
          ),
        );
      }).toList(),
    );
  }

  DateTime? _parseDate(dynamic value) {
    if (value is String && value.isNotEmpty) {
      return DateTime.tryParse(value);
    }
    return null;
  }
}

class _StartButton extends StatelessWidget {
  const _StartButton({required this.loading, required this.onPressed});

  final bool loading;
  final VoidCallback? onPressed;

  @override
  Widget build(BuildContext context) {
    return FilledButton(
      onPressed: onPressed,
      style: FilledButton.styleFrom(
        backgroundColor: AppColors.blue,
        foregroundColor: AppColors.white,
        minimumSize: const Size(0, 40),
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
        tapTargetSize: MaterialTapTargetSize.shrinkWrap,
      ),
      child: loading
          ? const SizedBox(
              width: 16,
              height: 16,
              child: CircularProgressIndicator(
                strokeWidth: 2,
                color: AppColors.white,
              ),
            )
          : const Row(
              mainAxisSize: MainAxisSize.min,
              children: <Widget>[
                Text('Start',
                    style:
                        TextStyle(fontSize: 13, fontWeight: FontWeight.w600)),
                SizedBox(width: 4),
                ClasrumIcon(ClasrumIconKind.startLearning, size: 16, color: AppColors.white),
              ],
            ),
    );
  }
}
