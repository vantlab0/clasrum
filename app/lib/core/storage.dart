import 'dart:convert';
import 'dart:convert' as convert;

import 'package:shared_preferences/shared_preferences.dart';

/// Small typed wrapper around [SharedPreferences].
///
/// Deliberately narrow: the classroom stores only the auth token, the lesson to
/// resume, and the student's last-used lesson defaults. Anything that must be
/// consistent across devices (profile defaults, preferences, notes) lives on
/// the backend.
class AppStorage {
  AppStorage(this._prefs);

  static Future<AppStorage> open() async {
    final prefs = await SharedPreferences.getInstance();
    return AppStorage(prefs);
  }

  final SharedPreferences _prefs;

  static const String _tokenKey = 'auth.token';
  static const String _lastLessonKey = 'lessons.last_id';
  static const String _levelKey = 'prefs.learning_level';
  static const String _durationKey = 'prefs.duration_minutes';
  static const String _lessonMetaKey = 'lessons.meta.v1';
  static const String _noteMetaKey = 'notes.meta.v1';

  // ---------------------------------------------------------------- auth ----

  String? get token {
    final value = _prefs.getString(_tokenKey);
    return (value == null || value.isEmpty) ? null : value;
  }

  Future<void> setToken(String? token) async {
    if (token == null || token.isEmpty) {
      await _prefs.remove(_tokenKey);
    } else {
      await _prefs.setString(_tokenKey, token);
    }
  }

  Future<void> clearAuthToken() async {
    await _prefs.remove(_tokenKey);
  }

  // ------------------------------------------------------------- lessons ----

  String? get lastLessonId {
    final value = _prefs.getString(_lastLessonKey);
    return (value == null || value.isEmpty) ? null : value;
  }

  Future<void> setLastLessonId(String? lessonId) async {
    if (lessonId == null || lessonId.isEmpty) {
      await _prefs.remove(_lastLessonKey);
    } else {
      await _prefs.setString(_lastLessonKey, lessonId);
    }
  }

  // --------------------------------------------------------- lesson prefs ----

  String? get defaultLearningLevel => _prefs.getString(_levelKey);

  Future<void> setDefaultLearningLevel(String? level) async {
    if (level == null || level.isEmpty) {
      await _prefs.remove(_levelKey);
    } else {
      await _prefs.setString(_levelKey, level);
    }
  }

  int? get defaultDurationMinutes => _prefs.getInt(_durationKey);

  Future<void> setDefaultDurationMinutes(int? minutes) async {
    if (minutes == null || minutes <= 0) {
      await _prefs.remove(_durationKey);
    } else {
      await _prefs.setInt(_durationKey, minutes);
    }
  }

  // ------------------------------------------------------- local metadata ----

  Map<String, Map<String, dynamic>> _readMeta(String key) {
    final raw = _prefs.getString(key);
    if (raw == null || raw.isEmpty) return <String, Map<String, dynamic>>{};
    try {
      final decoded = convert.jsonDecode(raw);
      if (decoded is! Map) return <String, Map<String, dynamic>>{};
      final out = <String, Map<String, dynamic>>{};
      decoded.forEach((k, v) {
        if (k is String && v is Map) {
          out[k] = Map<String, dynamic>.from(v);
        }
      });
      return out;
    } catch (_) {
      return <String, Map<String, dynamic>>{};
    }
  }

  Future<void> _writeMeta(
      String key, Map<String, Map<String, dynamic>> value) async {
    await _prefs.setString(key, convert.jsonEncode(value));
  }

  String? lessonTitleOverride(String lessonId) {
    final meta = _readMeta(_lessonMetaKey)[lessonId];
    final title = meta?['title'];
    return title is String && title.trim().isNotEmpty ? title.trim() : null;
  }

  DateTime? lessonLastOpened(String lessonId) {
    final meta = _readMeta(_lessonMetaKey)[lessonId];
    final raw = meta?['last_opened_at'];
    if (raw is String) return DateTime.tryParse(raw);
    return null;
  }

  bool isLessonHidden(String lessonId) =>
      _readMeta(_lessonMetaKey)[lessonId]?['hidden'] == true;

  Future<void> markLessonOpened(String lessonId, {DateTime? at}) async {
    if (lessonId.isEmpty) return;
    final all = _readMeta(_lessonMetaKey);
    final entry = all[lessonId] ?? <String, dynamic>{};
    entry['last_opened_at'] = (at ?? DateTime.now()).toIso8601String();
    all[lessonId] = entry;
    await _writeMeta(_lessonMetaKey, all);
  }

  Future<void> renameLesson(String lessonId, String title) async {
    if (lessonId.isEmpty) return;
    final all = _readMeta(_lessonMetaKey);
    final entry = all[lessonId] ?? <String, dynamic>{};
    final trimmed = title.trim();
    if (trimmed.isEmpty) {
      entry.remove('title');
    } else {
      entry['title'] = trimmed;
    }
    all[lessonId] = entry;
    await _writeMeta(_lessonMetaKey, all);
  }

  Future<void> hideLesson(String lessonId) async {
    if (lessonId.isEmpty) return;
    final all = _readMeta(_lessonMetaKey);
    final entry = all[lessonId] ?? <String, dynamic>{};
    entry['hidden'] = true;
    all[lessonId] = entry;
    await _writeMeta(_lessonMetaKey, all);
  }

  String? noteTitleOverride(String noteId) {
    final meta = _readMeta(_noteMetaKey)[noteId];
    final title = meta?['title'];
    return title is String && title.trim().isNotEmpty ? title.trim() : null;
  }

  DateTime? noteLastOpened(String noteId) {
    final meta = _readMeta(_noteMetaKey)[noteId];
    final raw = meta?['last_opened_at'];
    if (raw is String) return DateTime.tryParse(raw);
    return null;
  }

  bool isNoteHidden(String noteId) =>
      _readMeta(_noteMetaKey)[noteId]?['hidden'] == true;

  Future<void> markNoteOpened(String noteId, {DateTime? at}) async {
    if (noteId.isEmpty) return;
    final all = _readMeta(_noteMetaKey);
    final entry = all[noteId] ?? <String, dynamic>{};
    entry['last_opened_at'] = (at ?? DateTime.now()).toIso8601String();
    all[noteId] = entry;
    await _writeMeta(_noteMetaKey, all);
  }

  Future<void> renameNote(String noteId, String title) async {
    if (noteId.isEmpty) return;
    final all = _readMeta(_noteMetaKey);
    final entry = all[noteId] ?? <String, dynamic>{};
    final trimmed = title.trim();
    if (trimmed.isEmpty) {
      entry.remove('title');
    } else {
      entry['title'] = trimmed;
    }
    all[noteId] = entry;
    await _writeMeta(_noteMetaKey, all);
  }

  Future<void> hideNote(String noteId) async {
    if (noteId.isEmpty) return;
    final all = _readMeta(_noteMetaKey);
    final entry = all[noteId] ?? <String, dynamic>{};
    entry['hidden'] = true;
    all[noteId] = entry;
    await _writeMeta(_noteMetaKey, all);
  }

  // ------------------------------------------------------------- account ----

  /// Clears everything this device remembers about the signed-in student.
  /// Called on sign-out and after a destructive "delete my data" request.
  Future<void> clearAccountScopedData() async {
    await _prefs.remove(_tokenKey);
    await _prefs.remove(_lastLessonKey);
  }

  /// Clears all user-scoped local state/caches (including preferences) so data
  /// does not leak between accounts on the same device.
  /// Does not delete persistent cloud data.
  Future<void> clearUserScopedData() async {
    await _prefs.remove(_tokenKey);
    await _prefs.remove(_lastLessonKey);
    await _prefs.remove(_levelKey);
    await _prefs.remove(_durationKey);
    await _prefs.remove(_lessonMetaKey);
    await _prefs.remove(_noteMetaKey);
  }

  // ------------------------------------------------------------- quiz persistence ----

  /// Stores quiz completion data locally.
  Future<void> setQuizData({
    required String topic,
    required int questionCount,
    required int correctCount,
    required int durationMinutes,
    required Set<int> answeredIndices,
  }) async {
    final meta = <String, dynamic>{
      'topic': topic,
      'question_count': questionCount,
      'correct_count': correctCount,
      'duration_minutes': durationMinutes,
      'answered_indices': answeredIndices.toList(),
      'completed_at': DateTime.now().toIso8601String(),
    };
    await _prefs.setString('_quiz_meta_v1', jsonEncode(meta));
  }

  /// Retrieves stored quiz completion data, or returns null if none exists.
  Map<String, dynamic>? get quizData {
    final raw = _prefs.getString('_quiz_meta_v1');
    if (raw == null) return null;
    try {
      return jsonDecode(raw) as Map<String, dynamic>;
    } catch (_) {
      return null;
    }
  }

  /// Clears stored quiz completion data.
  Future<void> clearQuizData() async {
    await _prefs.remove('_quiz_meta_v1');
  }
}
