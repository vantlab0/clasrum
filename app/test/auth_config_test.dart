import 'package:ai_classroom/core/config.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  group('AppConfig Supabase config', () {
    test('reports missing Supabase values clearly', () {
      final config = AppConfig(
        apiBaseUrl: 'http://localhost:8000',
        supabaseUrl: '',
        supabaseAnonKey: '',
      );

      expect(config.hasSupabaseConfig, isFalse);
      expect(config.supabaseConfigError, contains('SUPABASE_URL'));
      expect(
        config.supabaseConfigError,
        contains('SUPABASE_ANON_KEY or SUPABASE_PUBLISHABLE_KEY'),
      );
    });

    test('accepts configured Supabase URL and anon key', () {
      final config = AppConfig(
        apiBaseUrl: 'http://localhost:8000',
        supabaseUrl: 'https://example.supabase.co',
        supabaseAnonKey: 'anon-key',
      );

      expect(config.hasSupabaseConfig, isTrue);
      expect(config.supabaseConfigError, isNull);
    });
  });
}
