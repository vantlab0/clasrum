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
  }
}
