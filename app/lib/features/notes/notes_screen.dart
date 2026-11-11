import 'dart:convert' as convert;

import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;

import '../../core/config.dart';
import '../../core/errors.dart';
import '../../core/formatters.dart';
import '../../core/storage.dart';

/// Notes screen: list of structured notes + session modal (CONTRACTS §5 / §10).
class NotesScreen extends StatefulWidget {
  const NotesScreen({
    super.key,
    required this.config,
    required this.storage,
  });

  final AppConfig config;
  final AppStorage storage;

  @override
  State<NotesScreen> createState() => _NotesScreenState();
}

class _NotesScreenState extends State<NotesScreen> {
  List<Map<String, dynamic>> _notes = [];
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _loadNotes();
  }

  Future<void> _loadNotes() async {
    try {
      final token = widget.storage.token;
      if (token == null) return;
      final response = await http.get(
        widget.config.apiUri('/notes'),
        headers: <String, String>{'Authorization': 'Bearer $token'},
      );
      if (response.statusCode == 200) {
        final data = convert.jsonDecode(response.body) as Map<String, dynamic>;
        final items = (data['items'] as List? ?? const <dynamic>[])
            .cast<Map<String, dynamic>>();
        if (mounted) {
          setState(() {
            _notes = items;
            _loading = false;
          });
        }
      }
    } catch (e) {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _generateNotes(String lessonId) async {
    final token = widget.storage.token;
    if (token == null) return;
    // Consent dialog (CONTRACTS §10: only call notes/generate with consent:true on yes).
    final confirmed = await showDialog<bool>(
          context: context,
          builder: (context) => AlertDialog(
            title: const Text('Generate lesson notes?'),
            content: const Text(
              'Would you like me to generate structured lesson notes from this tutorial? '
              'Notes are stored separately from the raw transcript.',
            ),
            actions: <Widget>[
              TextButton(
                onPressed: () => Navigator.pop(context, false),
                child: const Text('Not now'),
              ),
              FilledButton(
                onPressed: () => Navigator.pop(context, true),
                child: const Text('Generate'),
              ),
            ],
          ),
        ) ??
        false;
    if (!confirmed) return;

    try {
      final response = await http.post(
        widget.config.apiUri('/lessons/$lessonId/notes/generate'),
        headers: <String, String>{
          'Authorization': 'Bearer $token',
          'Content-Type': 'application/json',
        },
        body: convert.jsonEncode(<String, dynamic>{'consent': true}),
      );
      if (response.statusCode == 200) {
        await _loadNotes();
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(content: Text('Notes are being prepared.')),
          );
        }
      } else {
        final exc = ApiException.fromEnvelope(
          statusCode: response.statusCode,
          body: convert.jsonDecode(response.body) as Map<String, dynamic>,
        );
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text(exc.friendlyMessage)),
          );
        }
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(friendlyErrorMessage(e))));
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Notes')),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _notes.isEmpty
              ? const Center(child: Text('No structured notes yet.'))
              : ListView.builder(
                  itemCount: _notes.length,
                  itemBuilder: (context, index) {
                    final note = _notes[index];
                    return Card(
                      margin: const EdgeInsets.symmetric(
                          horizontal: 16, vertical: 6),
                      child: ListTile(
                        title: Text(note['title'] as String? ?? 'Untitled'),
                        subtitle: Text(
                          '${note['subject'] ?? ''} • ${noteStatusLabel(note['status'] as String? ?? '')} • ${note['word_count'] as int? ?? 0} words',
                        ),
                        trailing: note['status'] == 'ready'
                            ? const Icon(Icons.visibility, size: 20)
                            : const SizedBox(
                                width: 20,
                                height: 20,
                                child: CircularProgressIndicator(strokeWidth: 2)),
                        onTap: () {
                          final id = note['id'] as String;
                          Navigator.push(
                            context,
                            MaterialPageRoute<dynamic>(
                              builder: (context) => NoteDetailScreen(
                                config: widget.config,
                                storage: widget.storage,
                                noteId: id,
                              ),
                            ),
                          );
                        },
                      ),
                    );
                  },
                ),
      floatingActionButton: _notes.isNotEmpty
          ? FloatingActionButton.extended(
              onPressed: () {
                // Pick the most recent lesson to generate notes for.
                final lessonId = _notes.first['lesson_id'] as String?;
                if (lessonId != null) _generateNotes(lessonId);
              },
              label: const Text('Generate Notes'),
              icon: const Icon(Icons.auto_stories),
            )
          : null,
    );
  }
}

/// Detail view for one structured note.
class NoteDetailScreen extends StatelessWidget {
  const NoteDetailScreen({
    super.key,
    required this.config,
    required this.storage,
    required this.noteId,
  });

  final AppConfig config;
  final AppStorage storage;
  final String noteId;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Lesson Notes')),
      body: const Center(child: Text('Note content view — implement markdown rendering here.')),
    );
  }
}
