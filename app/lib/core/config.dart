import 'dart:core';

/// Runtime configuration for the AI Personal Classroom app.
///
/// The API origin is injected at build time:
///
/// ```sh
/// flutter run --dart-define=API_BASE_URL=http://192.168.1.20:8000
/// ```
///
/// Defaults to the Android emulator loopback alias (`10.0.2.2`) because that is
/// the most common development target. The iOS simulator reaches the host at
/// `http://localhost:8000`, and a physical device needs the host LAN address —
/// both are supplied through `--dart-define`.
class AppConfig {
  AppConfig({required String apiBaseUrl})
      : apiBaseUrl = _normalize(apiBaseUrl),
        _baseUri = Uri.parse(_normalize(apiBaseUrl));

  /// Android emulator → host machine mapping. Kept as a compile-time default so
  /// a plain `flutter run` works on an emulator without extra flags.
  static const String defaultApiBaseUrl = 'http://10.0.2.2:8000';

  /// Constructs the configuration from `--dart-define=API_BASE_URL=...`.
  factory AppConfig.fromEnvironment() {
    const raw = String.fromEnvironment(
      'API_BASE_URL',
      defaultValue: defaultApiBaseUrl,
    );
    return AppConfig(apiBaseUrl: raw);
  }

  /// Normalized REST origin, e.g. `http://10.0.2.2:8000`.
  final String apiBaseUrl;
  final Uri _baseUri;

  Uri get baseUri => _baseUri;

  /// WebSocket origin derived from [apiBaseUrl]: `http` → `ws`, `https` → `wss`.
  String get wsBaseUrl {
    switch (_baseUri.scheme) {
      case 'https':
      case 'wss':
        return _baseUri.replace(scheme: 'wss').toString();
      default:
        return _baseUri.replace(scheme: 'ws').toString();
    }
  }

  String get _apiRoot {
    final path = _baseUri.path.replaceAll(RegExp(r'/+$'), '');
    if (path.endsWith('/api/v1')) {
      return path;
    }
    return '$path/api/v1';
  }

  /// Builds a REST URL below `/api/v1`, e.g. `apiUri('/lessons')` →
  /// `http://10.0.2.2:8000/api/v1/lessons`.
  Uri apiUri(String path, {Map<String, String>? query}) {
    final normalized = path.startsWith('/') ? path : '/$path';
    return _baseUri.replace(
      path: '$_apiRoot$normalized',
      queryParameters: (query == null || query.isEmpty) ? null : query,
    );
  }

  /// Builds the lesson WebSocket URL per CONTRACTS §6 (`?token=<jwt>`).
  Uri lessonSocketUri(String lessonId, {required String token}) {
    return apiUri(
      '/ws/lessons/$lessonId',
      query: <String, String>{'token': token},
    ).replace(scheme: _baseUri.scheme == 'https' ? 'wss' : 'ws');
  }

  static String _normalize(String raw) {
    var value = raw.trim();
    if (value.isEmpty) {
      value = defaultApiBaseUrl;
    }
    if (!value.contains('://')) {
      value = 'http://$value';
    }
    final parsed = Uri.tryParse(value);
    if (parsed == null || parsed.host.isEmpty) {
      throw ArgumentError.value(raw, 'apiBaseUrl', 'Not a valid server URL');
    }
    var result = parsed.replace(
      path: parsed.path.replaceAll(RegExp(r'/+$'), ''),
    ).toString();
    while (result.endsWith('/')) {
      result = result.substring(0, result.length - 1);
    }
    return result;
  }
}
