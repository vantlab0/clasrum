import 'package:flutter/material.dart';

import 'core/config.dart';
import 'core/storage.dart';
import 'core/supabase_bootstrap.dart';
import 'core/theme.dart';
import 'core/widgets/clasrum_icons.dart';
import 'features/auth/auth_gate.dart';
import 'services/auth_service.dart';
import 'services/profile_service.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  final config = AppConfig.fromEnvironment();
  final storage = await AppStorage.open();
  await SupabaseBootstrap.initialize(config);

  final authService = AuthService(storage: storage);
  final profileService = ProfileService();
  // Ensure mirrored token/session state is settled before first frame so
  // AuthGate can route without flashing an incorrect screen.
  await authService.restoreSession();

  runApp(AiClassroomApp(
    config: config,
    storage: storage,
    authService: authService,
    profileService: profileService,
  ));
}

class AiClassroomApp extends StatelessWidget {
  const AiClassroomApp({
    super.key,
    required this.config,
    required this.storage,
    required this.authService,
    required this.profileService,
  });

  final AppConfig config;
  final AppStorage storage;
  final AuthService authService;
  final ProfileService profileService;

  @override
  Widget build(BuildContext context) {
    final initError = SupabaseBootstrap.initError;
    return MaterialApp(
      title: 'AI Personal Classroom',
      debugShowCheckedModeBanner: false,
      theme: AppTheme.dark(),
      home: initError != null
          ? _ConfigurationErrorScreen(message: initError)
          : AuthScope(
              authService: authService,
              profileService: profileService,
              child: AuthGate(config: config, storage: storage),
            ),
    );
  }
}

/// Provides auth/profile services to the widget tree using existing
/// InheritedWidget patterns (no additional state-management framework).
class AuthScope extends InheritedWidget {
  const AuthScope({
    super.key,
    required this.authService,
    required this.profileService,
    required super.child,
  });

  final AuthService authService;
  final ProfileService profileService;

  static AuthScope of(BuildContext context) {
    final scope = context.dependOnInheritedWidgetOfExactType<AuthScope>();
    if (scope == null) {
      throw StateError('AuthScope not found in widget tree');
    }
    return scope;
  }

  static AuthScope? maybeOf(BuildContext context) {
    return context.dependOnInheritedWidgetOfExactType<AuthScope>();
  }

  @override
  bool updateShouldNotify(AuthScope oldWidget) =>
      authService != oldWidget.authService ||
      profileService != oldWidget.profileService;
}

class _ConfigurationErrorScreen extends StatelessWidget {
  const _ConfigurationErrorScreen({required this.message});

  final String message;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFF080A0E),
      body: SafeArea(
        child: Center(
          child: Padding(
            padding: const EdgeInsets.all(24),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 520),
              child: Container(
                padding: const EdgeInsets.all(20),
                decoration: BoxDecoration(
                  color: const Color(0xFF12161D),
                  borderRadius: BorderRadius.circular(20),
                  border: Border.all(color: const Color(0xFF303743)),
                ),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Row(
                      children: [
                        ClasrumIcon(ClasrumIconKind.info, color: Colors.white, size: 22),
                        SizedBox(width: 10),
                        Expanded(
                          child: Text(
                            'Configuration required',
                            style: TextStyle(
                              color: Colors.white,
                              fontSize: 18,
                              fontWeight: FontWeight.w600,
                            ),
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: 12),
                    Text(
                      message,
                      style: const TextStyle(
                        color: Color(0xFFA1AAB8),
                        fontSize: 14,
                        height: 1.5,
                      ),
                    ),
                    const SizedBox(height: 12),
                    const Text(
                      'Example:\n'
                      'flutter run --dart-define=SUPABASE_URL=https://<project>.supabase.co '
                      '--dart-define=SUPABASE_ANON_KEY=<publishable-or-anon-key>',
                      style: TextStyle(
                        color: Color(0xFFA1AAB8),
                        fontSize: 12,
                        height: 1.5,
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}
