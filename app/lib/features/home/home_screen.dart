import 'dart:convert' as convert;

import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;

import '../../core/config.dart';
import '../../core/errors.dart';
import '../../core/formatters.dart';
import '../../core/storage.dart';

/// Home screen: topic entry + lesson list (CONTRACTS §5 / §10).
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
  String _level = 'beginner';
  int _duration = 30;
  bool _creating = false;
  String? _error;

  List<Map<String, dynamic>> _lessons = [];
  bool _loadingLessons = true;

  @override
  void initState() {
    super.initState();
    _loadLessons();
  }

  @override
  void dispose() {
    _topicController.dispose();
    super.dispose();
  }

  Future<void> _loadLessons() async {
    try {
      final token = widget.storage.token;
      if (token == null) return;
      final response = await http.get(
        widget.config.apiUri('/lessons'),
        headers: <String, String>{'Authorization': 'Bearer $token'},
      );
      if (response.statusCode == 200) {
        final data = convert.jsonDecode(response.body) as Map<String, dynamic>;
        final items = (data['items'] as List? ?? const <dynamic>[])
            .cast<Map<String, dynamic>>();
        if (mounted) {
          setState(() {
            _lessons = items;
            _loadingLessons = false;
          });
        }
      }
    } catch (e) {
      if (mounted) setState(() => _loadingLessons = false);
    }
  }

  Future<void> _createLesson() async {
    final topic = _topicController.text.trim();
    if (topic.length < 2) {
      setState(() => _error = 'Topic must be at least 2 characters.');
      return;
    }
    setState(() {
      _creating = true;
      _error = null;
    });
    try {
      final token = widget.storage.token;
      if (token == null) throw ApiException.unauthorized();
      await widget.storage.setDefaultLearningLevel(_level);
      await widget.storage.setDefaultDurationMinutes(_duration);
      final response = await http.post(
        widget.config.apiUri('/lessons'),
        headers: <String, String>{
          'Authorization': 'Bearer $token',
          'Content-Type': 'application/json',
        },
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
        final exc = ApiException.fromEnvelope(
          statusCode: response.statusCode,
          body: convert.jsonDecode(response.body) as Map<String, dynamic>,
        );
        setState(() => _error = exc.friendlyMessage);
      }
    } catch (e) {
      setState(() => _error = friendlyErrorMessage(e));
    } finally {
      if (mounted) setState(() => _creating = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('AI Classroom')),
      body: ListView(
        padding: const EdgeInsets.all(24),
        children: <Widget>[
          const Text('Start a new tutorial',
              style: TextStyle(fontSize: 20, fontWeight: FontWeight.w600)),
          const SizedBox(height: 16),
          TextField(
            controller: _topicController,
            decoration: const InputDecoration(
              labelText: 'What would you like to learn?',
              hintText: 'e.g. Introduction to Differentiation',
            ),
            maxLength: 500,
          ),
          const SizedBox(height: 16),
          Row(
            children: <Widget>[
              Expanded(
                child: DropdownButtonFormField<String>(
                  initialValue: _level,
                  decoration: const InputDecoration(labelText: 'Level'),
                  items: learningLevels
                      .map((l) => DropdownMenuItem(
                          value: l, child: Text(levelLabel(l))))
                      .toList(),
                  onChanged: (v) => setState(() => _level = v!),
                ),
              ),
              const SizedBox(width: 12),
              SizedBox(
                width: 110,
                child: DropdownButtonFormField<int>(
                  initialValue: _duration,
                  decoration: const InputDecoration(labelText: 'Minutes'),
                  items: supportedDurations
                      .map((d) => DropdownMenuItem(
                          value: d, child: Text(durationLabel(d))))
                      .toList(),
                  onChanged: (v) => setState(() => _duration = v!),
                ),
              ),
            ],
          ),
          const SizedBox(height: 16),
          if (_error != null)
            Container(
              padding: const EdgeInsets.all(12),
              color: const Color(0xFF2A1515),
              child: Text(_error!,
                  style: const TextStyle(color: Color(0xFFE39C9C))),
            ),
          const SizedBox(height: 8),
          FilledButton(
            onPressed: _creating ? null : _createLesson,
            child: _creating
                ? const SizedBox(
                    width: 20,
                    height: 20,
                    child: CircularProgressIndicator(strokeWidth: 2))
                : const Text('Start Lesson'),
          ),
          const SizedBox(height: 32),
          const Text('Recent lessons',
              style: TextStyle(fontSize: 18, fontWeight: FontWeight.w600)),
          const SizedBox(height: 12),
          if (_loadingLessons)
            const Center(child: CircularProgressIndicator())
          else if (_lessons.isEmpty)
            const Text('No lessons yet. Start your first tutorial above.')
          else
            ..._lessons.map((lesson) {
              final status = lesson['status'] as String? ?? '';
              return Card(
                margin: const EdgeInsets.only(bottom: 8),
                child: ListTile(
                  title: Text(lesson['topic'] as String? ?? 'Untitled'),
                  subtitle: Text(
                    '${lesson['subject'] ?? ''} • ${lessonStatusLabel(status)} • ${percentLabel((lesson['progress'] as num? ?? 0).toDouble())}',
                  ),
                  trailing: Text(clockDuration(
                      lesson['remaining_seconds'] as int? ?? 0)),
                  onTap: () {
                    final id = lesson['id'] as String;
                    widget.storage.setLastLessonId(id);
                    widget.onStartLesson(id);
                  },
                ),
              );
            }),
        ],
      ),
    );
  }
}
