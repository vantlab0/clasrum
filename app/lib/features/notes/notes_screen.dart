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
import '../../main.dart';
import '../../services/realtime_client.dart';

/// Notes screen: recent sessions + saved structured notes.
class NotesScreen extends StatefulWidget {
  const NotesScreen({
    super.key,
    required this.config,
    required this.storage,
    this.onContinueLesson,
  });

  final AppConfig config;
  final AppStorage storage;
  final void Function(String lessonId)? onContinueLesson;

  @override
  State<NotesScreen> createState() => _NotesScreenState();
}

class _NotesScreenState extends State<NotesScreen> {
  List<Map<String, dynamic>> _lessons = const [];
  List<Map<String, dynamic>> _notes = const [];
  bool _loading = true;
  final _searchController = TextEditingController();

  @override
  void initState() {
    super.initState();
    _load();
    _searchController.addListener(() => setState(() {}));
  }

  @override
  void dispose() {
    _searchController.dispose();
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

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final token = widget.storage.token;
      if (token == null) {
        if (mounted) setState(() => _loading = false);
        return;
      }
      final results = await Future.wait<http.Response>([
        http.get(widget.config.apiUri('/lessons'), headers: _authHeaders),
        http.get(widget.config.apiUri('/notes'), headers: _authHeaders),
      ]);

      if (results.any((r) => r.statusCode == 401 || r.statusCode == 403)) {
        await _handleAuthFailure();
        return;
      }

      List<Map<String, dynamic>> lessons = const [];
      List<Map<String, dynamic>> notes = const [];
      if (results[0].statusCode == 200) {
        final data =
            convert.jsonDecode(results[0].body) as Map<String, dynamic>;
        lessons = (data['items'] as List? ?? const <dynamic>[])
            .cast<Map<String, dynamic>>();
      }
      if (results[1].statusCode == 200) {
        final data =
            convert.jsonDecode(results[1].body) as Map<String, dynamic>;
        notes = (data['items'] as List? ?? const <dynamic>[])
            .cast<Map<String, dynamic>>();
      }
      if (mounted) {
        setState(() {
          _lessons = lessons;
          _notes = notes;
          _loading = false;
        });
      }
    } catch (_) {
      if (mounted) setState(() => _loading = false);
    }
  }

  String get _query => _searchController.text.trim().toLowerCase();

  List<Map<String, dynamic>> get _filteredLessons {
    if (_query.isEmpty) return _lessons;
    return _lessons.where((l) {
      final topic = (l['topic'] as String? ?? '').toLowerCase();
      final subject = (l['subject'] as String? ?? '').toLowerCase();
      return topic.contains(_query) || subject.contains(_query);
    }).toList();
  }

  List<Map<String, dynamic>> get _filteredNotes {
    if (_query.isEmpty) return _notes;
    return _notes.where((n) {
      final title = (n['title'] as String? ?? '').toLowerCase();
      final subject = (n['subject'] as String? ?? '').toLowerCase();
      return title.contains(_query) || subject.contains(_query);
    }).toList();
  }

  Map<String, dynamic>? _noteForLesson(String lessonId) {
    for (final n in _notes) {
      if (n['lesson_id'] == lessonId) return n;
    }
    return null;
  }

  Future<void> _generateNotes(String lessonId) async {
    final token = widget.storage.token;
    if (token == null) {
      await _handleAuthFailure();
      return;
    }
    final confirmed = await showDialog<bool>(
          context: context,
          barrierColor: Colors.black.withValues(alpha: 0.55),
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
              PrimaryActionButton(
                compact: true,
                onPressed: () => Navigator.pop(context, true),
                label: 'Generate',
              ),
            ],
          ),
        ) ??
        false;
    if (!confirmed) return;
    try {
      final response = await http.post(
        widget.config.apiUri('/lessons/$lessonId/notes/generate'),
        headers: _authHeaders,
        body: convert.jsonEncode(<String, dynamic>{'consent': true}),
      );
      if (!mounted) return;
      if (response.statusCode == 200) {
        await _load();
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(content: Text('Notes are being prepared.')),
          );
        }
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
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(exc.friendlyMessage)));
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(friendlyErrorMessage(e))));
      }
    }
  }

  void _showSessionModal(Map<String, dynamic> lesson) {
    final lessonId = lesson['id'] as String? ?? '';
    if (lessonId.isEmpty) return;
    final topic = lesson['topic'] as String? ?? 'Session';
    final note = _noteForLesson(lessonId);
    final noteId = note?['id'] as String?;

    GlassModal.show<void>(
      context,
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(
            topic,
            maxLines: 2,
            overflow: TextOverflow.ellipsis,
            style: Theme.of(context).textTheme.titleMedium,
          ),
          const SizedBox(height: 16),
          SizedBox(
            width: double.infinity,
            child: PrimaryActionButton(
              onPressed: () {
                Navigator.of(context).pop();
                if (noteId != null) {
                  _openNote(noteId, title: note?['title'] as String?);
                } else {
                  _generateNotes(lessonId);
                }
              },
              icon: ClasrumIconKind.notes,
              label: note != null ? 'View notes' : 'Generate notes',
            ),
          ),
          const SizedBox(height: 10),
          SizedBox(
            width: double.infinity,
            child: SecondaryActionButton(
              onPressed: widget.onContinueLesson == null
                  ? null
                  : () {
                      Navigator.of(context).pop();
                      widget.storage.setLastLessonId(lessonId);
                      widget.onContinueLesson!(lessonId);
                    },
              icon: ClasrumIconKind.resume,
              label: 'Continue Live Session',
            ),
          ),
        ],
      ),
    );
  }

  void _openNote(String noteId, {String? title}) {
    Navigator.push(
      context,
      MaterialPageRoute<void>(
        builder: (context) => NoteDetailScreen(
          config: widget.config,
          storage: widget.storage,
          noteId: noteId,
          title: title,
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final bottomPad = 110 + MediaQuery.paddingOf(context).bottom;
    final lessons = _filteredLessons;
    final notes = _filteredNotes;
    return Scaffold(
      backgroundColor: AppColors.background,
      body: SafeArea(
        bottom: false,
        child: _loading
            ? const Center(child: CircularProgressIndicator(strokeWidth: 2))
            : RefreshIndicator(
                color: AppColors.blue,
                onRefresh: _load,
                child: ListView(
                  padding: EdgeInsets.fromLTRB(20, 16, 20, bottomPad),
                  children: <Widget>[
                    Row(
                      children: <Widget>[
                        Expanded(
                          child: Text(
                            'Your Notes',
                            style: Theme.of(context)
                                .textTheme
                                .headlineSmall
                                ?.copyWith(fontWeight: FontWeight.w600),
                          ),
                        ),
                        ProfileMenuButton(
                          config: widget.config,
                          storage: widget.storage,
                          size: 36,
                        ),
                      ],
                    ),
                    const SizedBox(height: 12),
                    GlassContainer(
                      radius: 14,
                      padding: const EdgeInsets.symmetric(horizontal: 12),
                      blurSigma: 8,
                      backgroundOpacity: 0.28,
                      child: TextField(
                        controller: _searchController,
                        style: const TextStyle(color: AppColors.white),
                        decoration: const InputDecoration(
                          hintText: 'Search',
                          prefixIcon: Padding(
                            padding: EdgeInsets.only(left: 4, right: 4),
                            child: ClasrumIcon(ClasrumIconKind.search, size: 18, color: AppColors.muted),
                          ),
                          prefixIconConstraints:
                              BoxConstraints(minWidth: 34, minHeight: 40),
                          border: InputBorder.none,
                          enabledBorder: InputBorder.none,
                          focusedBorder: InputBorder.none,
                          filled: false,
                          isDense: true,
                          contentPadding: EdgeInsets.symmetric(vertical: 12),
                        ),
                      ),
                    ),
                    const SizedBox(height: 24),
                    _sectionHeading('Recent sessions'),
                    const SizedBox(height: 10),
                    if (lessons.isEmpty)
                      _emptyText('No recent sessions.')
                    else
                      ...lessons.take(12).map(_sessionTile),
                    const SizedBox(height: 24),
                    _sectionHeading('Saved notes'),
                    const SizedBox(height: 10),
                    if (notes.isEmpty)
                      _emptyText('No saved notes yet.')
                    else
                      ...notes.map(_noteTile),
                  ],
                ),
              ),
      ),
    );
  }

  Widget _sectionHeading(String text) => Text(
        text,
        style: const TextStyle(
          color: AppColors.muted,
          fontSize: 13,
          fontWeight: FontWeight.w600,
          letterSpacing: 0.2,
        ),
      );

  Widget _emptyText(String text) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 8),
        child: Text(text,
            style: const TextStyle(color: AppColors.muted, fontSize: 14)),
      );

  Widget _sessionTile(Map<String, dynamic> lesson) {
    final title = lesson['topic'] as String? ?? 'Untitled session';
    final updatedAt = _parseDate(lesson['updated_at'] ?? lesson['created_at']);
    final meta = <String>[
      if (updatedAt != null) relativeTime(updatedAt),
      lessonStatusLabel(lesson['status'] as String? ?? ''),
    ].where((s) => s.isNotEmpty).join(' · ');
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: GlassContainer(
        radius: 16,
        padding: EdgeInsets.zero,
        blurSigma: 6,
        backgroundOpacity: 0.28,
        child: ListTile(
          dense: true,
          contentPadding:
              const EdgeInsets.symmetric(horizontal: 14, vertical: 2),
          title: Text(title,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(
                  color: AppColors.white,
                  fontSize: 14,
                  fontWeight: FontWeight.w600)),
          subtitle: Text(meta,
              style: const TextStyle(color: AppColors.muted, fontSize: 12)),
          trailing: const ClasrumIcon(ClasrumIconKind.moreOptions, color: AppColors.muted, size: 20),
          onTap: () => _showSessionModal(lesson),
        ),
      ),
    );
  }

  Widget _noteTile(Map<String, dynamic> note) {
    final noteId = note['id'] as String? ?? '';
    final title = note['title'] as String? ?? 'Untitled note';
    final subject = note['subject'] as String? ?? note['topic'] as String?;
    final createdAt = _parseDate(note['created_at'] ?? note['updated_at']);
    final status = note['status'] as String? ?? '';
    final meta = <String>[
      if (subject != null && subject.isNotEmpty) subject,
      if (createdAt != null) dateLabel(createdAt),
    ].join(' · ');
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: GlassContainer(
        radius: 16,
        padding: EdgeInsets.zero,
        blurSigma: 6,
        backgroundOpacity: 0.28,
        child: ListTile(
          contentPadding:
              const EdgeInsets.symmetric(horizontal: 14, vertical: 4),
          title: Text(title,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(
                  color: AppColors.white,
                  fontSize: 15,
                  fontWeight: FontWeight.w600)),
          subtitle: Text(meta.isEmpty ? 'Saved note' : meta,
              style: const TextStyle(color: AppColors.muted, fontSize: 12)),
          trailing: Row(
            mainAxisSize: MainAxisSize.min,
            children: <Widget>[
              Container(
                padding:
                    const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                decoration: BoxDecoration(
                  borderRadius: BorderRadius.circular(999),
                  border: Border.all(
                      color: AppColors.border.withValues(alpha: 0.5)),
                ),
                child: Text(
                  status == 'ready' ? 'Note' : noteStatusLabel(status),
                  style:
                      const TextStyle(color: AppColors.muted, fontSize: 11),
                ),
              ),
              const SizedBox(width: 6),
              const ClasrumIcon(ClasrumIconKind.chevronRight, color: AppColors.muted, size: 18),
            ],
          ),
          onTap: noteId.isEmpty ? null : () => _openNote(noteId, title: title),
        ),
      ),
    );
  }

  DateTime? _parseDate(dynamic value) {
    if (value is String && value.isNotEmpty) return DateTime.tryParse(value);
    return null;
  }
}

/// Detail view for one structured note.
class NoteDetailScreen extends StatefulWidget {
  const NoteDetailScreen({
    super.key,
    required this.config,
    required this.storage,
    required this.noteId,
    this.title,
  });

  final AppConfig config;
  final AppStorage storage;
  final String noteId;
  final String? title;

  @override
  State<NoteDetailScreen> createState() => _NoteDetailScreenState();
}

class _NoteDetailScreenState extends State<NoteDetailScreen> {
  Map<String, dynamic>? _note;
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
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

  Future<void> _load() async {
    try {
      final token = widget.storage.token;
      if (token == null) {
        await _handleAuthFailure();
        return;
      }
      final response = await http.get(
        widget.config.apiUri('/notes/${widget.noteId}'),
        headers: <String, String>{'Authorization': 'Bearer $token'},
      );
      if (!mounted) return;
      if (response.statusCode == 200) {
        setState(() {
          _note = convert.jsonDecode(response.body) as Map<String, dynamic>;
          _loading = false;
        });
      } else if (response.statusCode == 401 || response.statusCode == 403) {
        await _handleAuthFailure();
      } else {
        setState(() {
          _loading = false;
          _error = 'Unable to load note.';
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _loading = false;
          _error = friendlyErrorMessage(e);
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final title = _note?['title'] as String? ?? widget.title ?? 'Lesson Notes';
    return Scaffold(
      backgroundColor: AppColors.background,
      appBar: AppBar(
        title: Text(title, maxLines: 1, overflow: TextOverflow.ellipsis),
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator(strokeWidth: 2))
          : _error != null
              ? Center(
                  child: Padding(
                    padding: const EdgeInsets.all(24),
                    child: Text(_error!,
                        style: const TextStyle(color: AppColors.muted)),
                  ),
                )
              : _buildContent(context),
    );
  }

  Widget _buildContent(BuildContext context) {
    final note = _note!;
    final status = note['status'] as String? ?? '';
    if (status != 'ready' && status.isNotEmpty) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Text(
            'Notes are ${noteStatusLabel(status).toLowerCase()}.',
            style: const TextStyle(color: AppColors.muted),
          ),
        ),
      );
    }
    final sections = (note['sections'] as List?)?.cast<dynamic>() ??
        const <dynamic>[];
    final markdown = note['content_markdown'] as String? ?? '';

    return SingleChildScrollView(
      padding: const EdgeInsets.fromLTRB(20, 8, 20, 32),
      child: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 720),
          child: Container(
            width: double.infinity,
            padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 22),
            decoration: BoxDecoration(
              color: AppColors.surface.withValues(alpha: 0.55),
              borderRadius: BorderRadius.circular(20),
              border: Border.all(color: AppColors.border.withValues(alpha: 0.4)),
            ),
            child: sections.isNotEmpty
                ? _SectionsView(sections: sections)
                : SelectableText(
                    markdown.isEmpty ? 'No note content available.' : markdown,
                    style: const TextStyle(
                      color: AppColors.white,
                      fontSize: 16,
                      height: 1.7,
                    ),
                  ),
          ),
        ),
      ),
    );
  }
}

class _SectionsView extends StatelessWidget {
  const _SectionsView({required this.sections});
  final List<dynamic> sections;

  @override
  Widget build(BuildContext context) {
    final children = <Widget>[];
    for (final raw in sections) {
      if (raw is! Map) continue;
      final section = Map<String, dynamic>.from(raw);
      final heading = section['heading'] as String?;
      final body = section['body'] as String? ?? '';
      final latex = section['latex'] as String?;
      if (heading != null && heading.trim().isNotEmpty) {
        if (children.isNotEmpty) children.add(const SizedBox(height: 20));
        children.add(Text(
          heading,
          style: const TextStyle(
            color: AppColors.white,
            fontSize: 20,
            fontWeight: FontWeight.w700,
            height: 1.3,
          ),
        ));
        children.add(const SizedBox(height: 8));
      }
      if (body.trim().isNotEmpty) {
        children.add(_BodyText(body));
      }
      if (latex != null && latex.trim().isNotEmpty) {
        children.add(const SizedBox(height: 10));
        children.add(Container(
          width: double.infinity,
          padding: const EdgeInsets.all(12),
          decoration: BoxDecoration(
            color: AppColors.background.withValues(alpha: 0.5),
            borderRadius: BorderRadius.circular(12),
            border: Border.all(color: AppColors.border.withValues(alpha: 0.35)),
          ),
          child: SelectableText(
            latex,
            style: const TextStyle(
              color: AppColors.white,
              fontSize: 15,
              height: 1.6,
              fontFamily: 'monospace',
            ),
          ),
        ));
      }
    }
    if (children.isEmpty) {
      return const Text('No note content available.',
          style: TextStyle(color: AppColors.muted));
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: children,
    );
  }
}

class _BodyText extends StatelessWidget {
  const _BodyText(this.text);
  final String text;

  @override
  Widget build(BuildContext context) {
    final lines = text.split('\n');
    final spans = <Widget>[];
    for (final line in lines) {
      final trimmed = line.trimRight();
      if (trimmed.trim().isEmpty) {
        spans.add(const SizedBox(height: 10));
        continue;
      }
      final isBullet = trimmed.trimLeft().startsWith('- ') ||
          trimmed.trimLeft().startsWith('* ') ||
          RegExp(r'^\d+\.\s').hasMatch(trimmed.trimLeft());
      if (isBullet) {
        final clean = trimmed.trimLeft().replaceFirst(RegExp(r'^([-*]|\d+\.)\s*'), '');
        spans.add(Padding(
          padding: const EdgeInsets.only(left: 4, bottom: 6),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              const Text('•  ',
                  style: TextStyle(color: AppColors.muted, height: 1.7)),
              Expanded(
                child: SelectableText(
                  clean,
                  style: const TextStyle(
                      color: AppColors.white, fontSize: 16, height: 1.7),
                ),
              ),
            ],
          ),
        ));
      } else {
        spans.add(Padding(
          padding: const EdgeInsets.only(bottom: 6),
          child: SelectableText(
            trimmed,
            style: const TextStyle(
                color: AppColors.white, fontSize: 16, height: 1.7),
          ),
        ));
      }
    }
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: spans);
  }
}
