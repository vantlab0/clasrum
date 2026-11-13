import 'package:supabase_flutter/supabase_flutter.dart';

import 'config.dart';

/// Centralized Supabase initialization.
///
/// Call once during app startup after [WidgetsFlutterBinding.ensureInitialized].
class SupabaseBootstrap {
  SupabaseBootstrap._();

  static String? _initError;

  /// Non-null when initialization failed or configuration is missing.
  static String? get initError => _initError;

  static bool get isInitialized => _initError == null;

  static SupabaseClient? get clientOrNull {
    try {
      return Supabase.instance.client;
    } catch (_) {
      return null;
    }
  }

  /// Initializes Supabase using build-time configuration.
  ///
  /// If required values are missing, initialization is skipped and a clear
  /// configuration error is recorded so the app can fail gracefully.
  static Future<void> initialize(AppConfig config) async {
    final configError = config.supabaseConfigError;
    if (configError != null) {
      _initError = configError;
      return;
    }
    try {
      await Supabase.initialize(
        url: config.supabaseUrl,
        anonKey: config.supabaseAnonKey,
      );
      _initError = null;
    } catch (e) {
      _initError = 'Failed to initialize Supabase: $e';
    }
  }
}
