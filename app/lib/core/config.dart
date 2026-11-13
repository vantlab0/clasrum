import 'dart:core';

/// Runtime configuration for the AI Personal Classroom app.
///
/// The API origin and Supabase credentials are injected at build time:
///
/// ```sh
/// flutter run \
///   --dart-define=API_BASE_URL=http://192.168.1.20:8000 \
///   --dart-define=SUPABASE_URL=https://<project>.supabase.co \
///   --dart-define=SUPABASE_ANON_KEY=<anon-or-publishable-key>
/// ```
///
/// Defaults to the Android emulator loopback alias (`10.0.2.2`) because that is
/// the most common development target. The iOS simulator reaches the host at
/// `http://localhost:8000`, and a physical device needs the host LAN address —
/// both are supplied through `--dart-define`.
class AppConfig {
  AppConfig({
    required String apiBaseUrl,
    required this.supabaseUrl,
    required this.supabaseAnonKey,
  })  : apiBaseUrl = _normalize(apiBaseUrl),
        _baseUri = Uri.parse(_normalize(apiBaseUrl));

  /// Production backend. Can be overridden at build/run time with:
  /// `--dart-define=API_BASE_URL=https://realtime.avelut.xyz`
  static const String defaultApiBaseUrl = 'https://realtime.avelut.xyz';

  /// Constructs the configuration from build-time environment values.
  factory AppConfig.fromEnvironment() {
    const raw = String.fromEnvironment(
      'API_BASE_URL',
      defaultValue: defaultApiBaseUrl,
    );
    const supabaseUrl = String.fromEnvironment('SUPABASE_URL', defaultValue: '');
    // Accept either SUPABASE_ANON_KEY or SUPABASE_PUBLISHABLE_KEY.
    const anon = String.fromEnvironment('SUPABASE_ANON_KEY', defaultValue: '');
    const publishable =
        String.fromEnvironment('SUPABASE_PUBLISHABLE_KEY', defaultValue: '');
    return AppConfig(
      apiBaseUrl: raw,
      supabaseUrl: supabaseUrl.trim(),
      supabaseAnonKey: (anon.isNotEmpty ? anon : publishable).trim(),
    );
  }

  /// Normalized REST origin, e.g. `http://10.0.2.2:8000`.
  final String apiBaseUrl;
  final Uri _baseUri;

  /// Supabase project URL (e.g. https://xxxx.supabase.co).
  final String supabaseUrl;

  /// Supabase publishable/anon key (never a service-role key).
  final String supabaseAnonKey;

  bool get hasSupabaseConfig =>
      supabaseUrl.isNotEmpty && supabaseAnonKey.isNotEmpty;

  String? get supabaseConfigError {
    if (hasSupabaseConfig) return null;
    final missing = <String>[];
    if (supabaseUrl.isEmpty) missing.add('SUPABASE_URL');
    if (supabaseAnonKey.isEmpty) {
      missing.add('SUPABASE_ANON_KEY or SUPABASE_PUBLISHABLE_KEY');
    }
    return 'Supabase is not configured. Missing: ${missing.join(', ')}. '
        'Provide them via --dart-define at build/run time.';
  }

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
