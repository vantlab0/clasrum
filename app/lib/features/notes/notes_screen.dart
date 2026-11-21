import 'dart:async';
import 'dart:convert' as convert;

import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;

import '../../core/config.dart';
import '../../core/errors.dart';
import '../../core/formatters.dart';
import '../../core/storage.dart';
import '../../core/theme.dart';
import '../../core/widgets/pwony_icons.dart';
import '../../core/widgets/glass_widgets.dart';
import '../../core/widgets/profile_menu.dart';
import '../../main.dart';
import '../../services/realtime_client.dart';
import '../../features/flashcards/flashcards_screen.dart';
import '../../features/test_yourself/test_yourself_screen.dart';

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

enum _CardMenuAction { rename, delete, share }

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
        if (mounted) {
          setState(() {
            _lessons = const [];
            _notes = const [];
            _loading = false;
          });
        }
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
    final base = _lessons.where((l) {
      final id = l['id'] as String? ?? '';
      return !widget.storage.isLessonHidden(id);
    }).toList();
    if (_query.isEmpty) return base;
    return base.where((l) {
      final topic = _lessonTitle(l).toLowerCase();
      final subject = (l['subject'] as String? ?? '').toLowerCase();
      return topic.contains(_query) || subject.contains(_query);
    }).toList();
  }

  List<Map<String, dynamic>> get _filteredNotes {
    final base = _notes.where((n) {
      final id = n['id'] as String? ?? '';
      return !widget.storage.isNoteHidden(id);
    }).toList();
    if (_query.isEmpty) return base;
    return base.where((n) {
      final title = _noteTitle(n).toLowerCase();
      final subject = (n['subject'] as String? ?? '').toLowerCase();
      final topic = (n['topic'] as String? ?? '').toLowerCase();
      return title.contains(_query) ||
          subject.contains(_query) ||
          topic.contains(_query);
    }).toList();
  }

  Map<String, dynamic>? _noteForLesson(String lessonId) {
    for (final n in _notes) {
      if (n['lesson_id'] == lessonId) return n;
    }
    return null;
  }

  String _lessonTitle(Map<String, dynamic> lesson) {
    final id = lesson['id'] as String? ?? '';
    return widget.storage.lessonTitleOverride(id) ??
        (lesson['topic'] as String? ?? 'Untitled session');
  }

  String _noteTitle(Map<String, dynamic> note) {
    final id = note['id'] as String? ?? '';
    return widget.storage.noteTitleOverride(id) ??
        (note['title'] as String? ?? 'Untitled note');
  }

  DateTime? _lessonDisplayTime(Map<String, dynamic> lesson) {
    final id = lesson['id'] as String? ?? '';
    return widget.storage.lessonLastOpened(id) ??
        _parseDate(lesson['updated_at'] ?? lesson['created_at']);
  }

  DateTime? _noteDisplayTime(Map<String, dynamic> note) {
    final id = note['id'] as String? ?? '';
    return widget.storage.noteLastOpened(id) ??
        _parseDate(note['updated_at'] ?? note['created_at']);
  }

  String _unsplashPreviewUrl({required String id, required String topic}) {
    final cleanTitle =
        topic.trim().isEmpty ? 'education learning notes' : topic.trim();
    final prompt = Uri.encodeComponent(
      '$cleanTitle, educational topic illustration, high quality',
    );
    final seed = (id.isNotEmpty ? id : cleanTitle).hashCode.abs() % 1000000;
    // Title-based preview image for cards (stable per item via seed).
    return 'https://image.pollinations.ai/prompt/$prompt?width=800&height=600&nologo=true&seed=$seed&model=turbo';
  }

  void _showComingSoonShare() {
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(content: Text('Sharing will be available soon.')),
    );
  }

  Future<void> _renameLesson(Map<String, dynamic> lesson) async {
    final id = lesson['id'] as String? ?? '';
    if (id.isEmpty) return;
    final controller = TextEditingController(text: _lessonTitle(lesson));
    final result = await showDialog<String>(
      context: context,
      barrierColor: Colors.black.withValues(alpha: 0.55),
      builder: (context) => GlassModal(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            const Text(
              'Rename session',
              style: TextStyle(
                  color: AppColors.white,
                  fontSize: 18,
                  fontWeight: FontWeight.w600),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: controller,
              autofocus: true,
              style: const TextStyle(color: AppColors.white),
              decoration: const InputDecoration(hintText: 'Session title'),
            ),
            const SizedBox(height: 16),
            Row(
              children: <Widget>[
                Expanded(
                  child: SecondaryActionButton(
                    onPressed: () => Navigator.of(context).pop(),
                    label: 'Cancel',
                    compact: true,
                  ),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: PrimaryActionButton(
                    onPressed: () => Navigator.of(context).pop(controller.text),
                    label: 'Save',
                    compact: true,
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
    controller.dispose();
    if (result == null) return;
    await widget.storage.renameLesson(id, result);
    if (mounted) setState(() {});
  }

  Future<void> _deleteLesson(Map<String, dynamic> lesson) async {
    final id = lesson['id'] as String? ?? '';
    if (id.isEmpty) return;
    final confirmed = await _confirmDelete(
        'Delete session?', 'This will remove the session from this device.');
    if (!confirmed) return;
    await widget.storage.hideLesson(id);
    if (mounted) setState(() {});
  }

  Future<void> _renameNote(Map<String, dynamic> note) async {
    final id = note['id'] as String? ?? '';
    if (id.isEmpty) return;
    final controller = TextEditingController(text: _noteTitle(note));
    final result = await showDialog<String>(
      context: context,
      barrierColor: Colors.black.withValues(alpha: 0.55),
      builder: (context) => GlassModal(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            const Text(
              'Rename note',
              style: TextStyle(
                  color: AppColors.white,
                  fontSize: 18,
                  fontWeight: FontWeight.w600),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: controller,
              autofocus: true,
              style: const TextStyle(color: AppColors.white),
              decoration: const InputDecoration(hintText: 'Note title'),
            ),
            const SizedBox(height: 16),
            Row(
              children: <Widget>[
                Expanded(
                  child: SecondaryActionButton(
                    onPressed: () => Navigator.of(context).pop(),
                    label: 'Cancel',
                    compact: true,
                  ),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: PrimaryActionButton(
                    onPressed: () => Navigator.of(context).pop(controller.text),
                    label: 'Save',
                    compact: true,
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
    controller.dispose();
    if (result == null) return;
    await widget.storage.renameNote(id, result);
    if (mounted) setState(() {});
  }

  Future<void> _deleteNote(Map<String, dynamic> note) async {
    final id = note['id'] as String? ?? '';
    if (id.isEmpty) return;
    final confirmed = await _confirmDelete(
        'Delete note?', 'This will remove the note from this device.');
    if (!confirmed) return;
    await widget.storage.hideNote(id);
    if (mounted) setState(() {});
  }

  Future<bool> _confirmDelete(String title, String message) async {
    return await showDialog<bool>(
          context: context,
          barrierColor: Colors.black.withValues(alpha: 0.55),
          builder: (context) => GlassModal(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text(
                  title,
                  style: const TextStyle(
                      color: AppColors.white,
                      fontSize: 18,
                      fontWeight: FontWeight.w600),
                ),
                const SizedBox(height: 10),
                Text(message,
                    style:
                        const TextStyle(color: AppColors.muted, fontSize: 14)),
                const SizedBox(height: 16),
                Row(
                  children: <Widget>[
                    Expanded(
                      child: SecondaryActionButton(
                        onPressed: () => Navigator.of(context).pop(false),
                        label: 'Cancel',
                        compact: true,
                      ),
                    ),
                    const SizedBox(width: 10),
                    Expanded(
                      child: PrimaryActionButton(
                        onPressed: () => Navigator.of(context).pop(true),
                        label: 'Delete',
                        compact: true,
                      ),
                    ),
                  ],
                ),
              ],
            ),
          ),
        ) ??
        false;
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

  Future<void> _showNoteMenu(Map<String, dynamic> note) async {
    final bool? result = await showDialog<bool>(
          context: context,
          barrierColor: Colors.black.withValues(alpha: 0.55),
          builder: (context) => GlassModal(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                const Text(
                  'Note actions',
                  style: TextStyle(
                      color: AppColors.white,
                      fontSize: 18,
                      fontWeight: FontWeight.w600),
                ),
                const SizedBox(height: 12),
                GlassContainer(
                  radius: AppRadius.md,
                  padding: const EdgeInsets.all(12),
                  blurSigma: 6,
                  backgroundOpacity: 0.7,
                  borderOpacity: 0.5,
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: <Widget>[
                      _MenuItem(
                        label: 'Generate Quiz',
                        icon: PwonyIconKind.notes,
                        onTap: () {
                          Navigator.of(context).pop(true);
                          _generateQuizFromNote(note);
                        },
                      ),
                      const Divider(
                        color: AppColors.border,
                        height: 8,
                      ),
                      _MenuItem(
                        label: 'Generate Flashcards',
                        icon: PwonyIconKind.flashcard,
                        onTap: () {
                          Navigator.of(context).pop(true);
                          _generateFlashcardsFromNote(note);
                        },
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),
        ) ??
        false;
    if (result == true) {
      // Action performed
    }
  }

  Future<void> _generateQuizFromNote(Map<String, dynamic> note) async {
    final noteId = note['id'] as String?;
    if (noteId == null || noteId.isEmpty) return;
    // Get note title for reference and pre-populate topic
    final noteTitle = _noteTitle(note);
    // Get actual note content from the saved note (content_markdown field)
    final noteContent = note['content_markdown'] as String? ?? '';
    // Close the contextual menu; the _MenuItem handler already popped it.
    // Navigate to test yourself screen with note title and content
    Navigator.of(context)
        .push(
      MaterialPageRoute<void>(
        builder: (context) => TestYourselfScreen(
          config: widget.config,
          storage: widget.storage,
          initialTopic: noteTitle,
          initialNoteContent: noteContent.isNotEmpty ? noteContent : null,
        ),
      ),
    )
        .then((_) {
      // After returning from test, we could reload or show results
    });
    // Show indication that quiz generation was triggered from note
    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Generating quiz from note: $noteTitle')),
      );
    }
  }

  Future<void> _generateFlashcardsFromNote(Map<String, dynamic> note) async {
    final noteId = note['id'] as String?;
    if (noteId == null || noteId.isEmpty) return;
    // Get note title for reference
    final noteTitle = _noteTitle(note);
    // Get note content for flashcard generation
    final noteContent = note['content_markdown'] as String? ?? '';
    // Close the contextual menu; the _MenuItem handler already popped it.
    // Navigate to flashcards screen with note context
    Navigator.of(context)
        .push(
      MaterialPageRoute<void>(
        builder: (context) => FlashcardsScreen(
          config: widget.config,
          storage: widget.storage,
          initialNoteTitle: noteTitle,
          initialNoteContent: noteContent.isNotEmpty ? noteContent : null,
          initialLessonId: note['lesson_id'] as String?,
          initialSubject: note['subject'] as String?,
        ),
      ),
    )
        .then((_) {
      // After returning from flashcards, we could reload or show results
    });
    // Show indication that flashcard generation was triggered from note
    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Generating flashcards from note: $noteTitle')),
      );
    }
  }

  void _showSessionModal(Map<String, dynamic> lesson) {
    final lessonId = lesson['id'] as String? ?? '';
    if (lessonId.isEmpty) return;
    final topic = _lessonTitle(lesson);
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
                  _openNote(noteId, title: _noteTitle(note!));
                } else {
                  _generateNotes(lessonId);
                }
              },
              icon: PwonyIconKind.notes,
              label: note != null ? 'View notes' : 'Generate notes',
            ),
          ),
          const SizedBox(height: 10),
          SizedBox(
            width: double.infinity,
            child: SecondaryActionButton(
              onPressed: widget.onContinueLesson == null
                  ? null
                  : () async {
                      Navigator.of(context).pop();
                      await widget.storage.setLastLessonId(lessonId);
                      await widget.storage.markLessonOpened(lessonId);
                      if (mounted) setState(() {});
                      widget.onContinueLesson!(lessonId);
                    },
              icon: PwonyIconKind.resume,
              label: 'Continue Live Session',
            ),
          ),
        ],
      ),
    );
  }

  Future<void> _openNote(String noteId, {String? title}) async {
    if (noteId.isEmpty) return;
    await widget.storage.markNoteOpened(noteId);
    if (mounted) setState(() {});
    if (!mounted) return;
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
                            child: PwonyIcon(PwonyIconKind.search,
                                size: 18, color: AppColors.muted),
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
                      _cardGrid(
                        itemCount: lessons.take(12).length,
                        itemBuilder: (context, index) {
                          final lesson = lessons[index];
                          final title = _lessonTitle(lesson);
                          final displayTime = _lessonDisplayTime(lesson);
                          final timestamp = displayTime != null
                              ? relativeTime(displayTime)
                              : '—';
                          final id = lesson['id'] as String? ?? '';
                          return _ImageCard(
                            imageUrl: _unsplashPreviewUrl(id: id, topic: title),
                            title: title,
                            timestamp: timestamp,
                            onTap: () => _showSessionModal(lesson),
                            onMenuSelected: (action) {
                              switch (action) {
                                case _CardMenuAction.rename:
                                  _renameLesson(lesson);
                                  break;
                                case _CardMenuAction.delete:
                                  _deleteLesson(lesson);
                                  break;
                                case _CardMenuAction.share:
                                  _showComingSoonShare();
                                  break;
                              }
                            },
                          );
                        },
                      ),
                    const SizedBox(height: 24),
                    _sectionHeading('Saved notes'),
                    const SizedBox(height: 10),
                    if (notes.isEmpty)
                      _emptyText('No saved notes yet.')
                    else
                      _cardGrid(
                        itemCount: notes.length,
                        itemBuilder: (context, index) {
                          final note = notes[index];
                          final title = _noteTitle(note);
                          final displayTime = _noteDisplayTime(note);
                          final timestamp = displayTime != null
                              ? relativeTime(displayTime)
                              : '—';
                          final id = note['id'] as String? ?? '';
                          return _ImageCard(
                            imageUrl: _unsplashPreviewUrl(id: id, topic: title),
                            title: title,
                            timestamp: timestamp,
                            onTap: () => _openNote(id, title: title),
                            onLongPress: () => _showNoteMenu(note),
                            onMenuSelected: (action) {
                              switch (action) {
                                case _CardMenuAction.rename:
                                  _renameNote(note);
                                  break;
                                case _CardMenuAction.delete:
                                  _deleteNote(note);
                                  break;
                                case _CardMenuAction.share:
                                  _showComingSoonShare();
                                  break;
                              }
                            },
                          );
                        },
                      ),
                  ],
                ),
              ),
      ),
    );
  }

  Widget _cardGrid({
    required int itemCount,
    required Widget Function(BuildContext, int) itemBuilder,
  }) {
    return GridView.builder(
      shrinkWrap: true,
      physics: const NeverScrollableScrollPhysics(),
      gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
        crossAxisCount: 2,
        mainAxisSpacing: 12,
        crossAxisSpacing: 12,
        childAspectRatio: 0.78,
      ),
      itemCount: itemCount,
      itemBuilder: itemBuilder,
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

  DateTime? _parseDate(dynamic value) {
    if (value is String && value.isNotEmpty) return DateTime.tryParse(value);
    return null;
  }
}

class _ImageCard extends StatelessWidget {
  const _ImageCard({
    required this.imageUrl,
    required this.title,
    required this.timestamp,
    required this.onTap,
    this.onLongPress,
    required this.onMenuSelected,
  });

  final String imageUrl;
  final String title;
  final String timestamp;
  final VoidCallback? onTap;
  final VoidCallback? onLongPress;
  final ValueChanged<_CardMenuAction> onMenuSelected;

  @override
  Widget build(BuildContext context) {
    return ClipRRect(
      borderRadius: BorderRadius.circular(20),
      child: Material(
        color: AppColors.surface,
        child: InkWell(
          onTap: onTap,
          onLongPress: onLongPress,
          child: Stack(
            fit: StackFit.expand,
            children: <Widget>[
              Image.network(
                imageUrl,
                fit: BoxFit.cover,
                errorBuilder: (context, error, stackTrace) => Container(
                  color: AppColors.surfaceHigh,
                  alignment: Alignment.center,
                  child: const PwonyIcon(
                    PwonyIconKind.notes,
                    size: 28,
                    color: AppColors.muted,
                  ),
                ),
                loadingBuilder: (context, child, loadingProgress) {
                  if (loadingProgress == null) return child;
                  return Container(
                    color: AppColors.surfaceHigh,
                    alignment: Alignment.center,
                    child: const SizedBox(
                      width: 20,
                      height: 20,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    ),
                  );
                },
              ),
              Container(
                decoration: BoxDecoration(
                  gradient: LinearGradient(
                    begin: Alignment.topCenter,
                    end: Alignment.bottomCenter,
                    stops: const <double>[0.0, 0.45, 0.7, 1.0],
                    colors: <Color>[
                      Colors.transparent,
                      Colors.black.withValues(alpha: 0.10),
                      Colors.black.withValues(alpha: 0.55),
                      Colors.black.withValues(alpha: 0.85),
                    ],
                  ),
                ),
              ),
              Positioned(
                top: 6,
                right: 6,
                child: Material(
                  color: Colors.black.withValues(alpha: 0.25),
                  shape: const CircleBorder(),
                  child: PopupMenuButton<_CardMenuAction>(
                    tooltip: 'Options',
                    icon: const PwonyIcon(
                      PwonyIconKind.moreOptions,
                      size: 18,
                      color: AppColors.white,
                    ),
                    onSelected: onMenuSelected,
                    color: AppColors.surfaceHigh,
                    itemBuilder: (context) =>
                        const <PopupMenuEntry<_CardMenuAction>>[
                      PopupMenuItem<_CardMenuAction>(
                        value: _CardMenuAction.rename,
                        child: Text('Rename'),
                      ),
                      PopupMenuItem<_CardMenuAction>(
                        value: _CardMenuAction.delete,
                        child: Text('Delete'),
                      ),
                      PopupMenuItem<_CardMenuAction>(
                        value: _CardMenuAction.share,
                        child: Text('Share'),
                      ),
                    ],
                  ),
                ),
              ),
              Align(
                alignment: Alignment.bottomCenter,
                child: Padding(
                  padding: const EdgeInsets.fromLTRB(12, 10, 10, 10),
                  child: Row(
                    crossAxisAlignment: CrossAxisAlignment.end,
                    children: <Widget>[
                      Expanded(
                        child: Column(
                          mainAxisSize: MainAxisSize.min,
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: <Widget>[
                            Text(
                              title,
                              maxLines: 2,
                              overflow: TextOverflow.ellipsis,
                              style: const TextStyle(
                                color: AppColors.white,
                                fontSize: 14,
                                fontWeight: FontWeight.w600,
                                height: 1.2,
                              ),
                            ),
                            const SizedBox(height: 4),
                            Text(
                              timestamp,
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: TextStyle(
                                color: AppColors.white.withValues(alpha: 0.85),
                                fontSize: 11,
                              ),
                            ),
                          ],
                        ),
                      ),
                      const SizedBox(width: 6),
                      const Padding(
                        padding: EdgeInsets.only(bottom: 2),
                        child: PwonyIcon(
                          PwonyIconKind.chevronRight,
                          size: 16,
                          color: AppColors.white,
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
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
        if (mounted) {
          setState(() {
            _loading = false;
            _error = 'Unable to load note.';
          });
        }
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
        if (mounted) {
          setState(() {
            _loading = false;
            _error =
                'Unable to load note from the server. Please try again later.';
          });
        }
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
    final sections =
        (note['sections'] as List?)?.cast<dynamic>() ?? const <dynamic>[];
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
              border:
                  Border.all(color: AppColors.border.withValues(alpha: 0.4)),
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

class _MenuItem extends StatelessWidget {
  const _MenuItem({
    required this.label,
    required this.icon,
    required this.onTap,
  });

  final String label;
  final PwonyIconKind icon;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Material(
      type: MaterialType.transparency,
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(10),
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 10),
          child: Row(
            children: <Widget>[
              PwonyIcon(icon, size: 18, color: AppColors.muted),
              const SizedBox(width: 12),
              Expanded(
                child: Text(
                  label,
                  style: const TextStyle(
                    color: AppColors.white,
                    fontSize: 15,
                    fontWeight: FontWeight.w500,
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
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
        final clean =
            trimmed.trimLeft().replaceFirst(RegExp(r'^([-*]|\d+\.)\s*'), '');
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
    return Column(
        crossAxisAlignment: CrossAxisAlignment.start, children: spans);
  }
}
