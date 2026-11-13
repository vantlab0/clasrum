import 'package:flutter/material.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

import '../../app_shell.dart';
import '../../core/config.dart';
import '../../core/storage.dart';
import '../../main.dart';
import 'login_screen.dart';
import 'verify_email_screen.dart';

/// Routes between authentication flow and the main app based on Supabase auth state.
class AuthGate extends StatefulWidget {
  const AuthGate({
    super.key,
    required this.config,
    required this.storage,
  });

  final AppConfig config;
  final AppStorage storage;

  @override
  State<AuthGate> createState() => _AuthGateState();
}

class _AuthGateState extends State<AuthGate> {
  bool _hasAuthenticatedShell = false;

  bool _isVerifiedAndSignedIn(Session? session, User? user) {
    if (session == null || user == null) return false;
    // A valid authenticated session is authoritative for access. If the
    // project requires email confirmation, Supabase will not issue a confirmed
    // session until verification is complete.
    return session.accessToken.isNotEmpty;
  }

  @override
  Widget build(BuildContext context) {
    final auth = AuthScope.of(context).authService;
    return AnimatedBuilder(
      animation: auth,
      builder: (context, _) {
        final session = auth.currentSession;
        final user = auth.currentUser;
        final signedIn = _isVerifiedAndSignedIn(session, user);

        if (signedIn) {
          _hasAuthenticatedShell = true;
          return AppShell(
            key: ValueKey<String>('app-shell-${user?.id ?? session?.user.id}'),
            config: widget.config,
            storage: widget.storage,
          );
        }

        // Leaving authenticated state: ensure no protected pushed routes remain.
        if (_hasAuthenticatedShell) {
          _hasAuthenticatedShell = false;
          WidgetsBinding.instance.addPostFrameCallback((_) {
            final nav = Navigator.maybeOf(context, rootNavigator: true);
            nav?.popUntil((route) => route.isFirst);
          });
        }

        // If a user exists but is not confirmed and there is no active session,
        // show verification state. (Login screen may also navigate here explicitly.)
        if (user != null &&
            user.emailConfirmedAt == null &&
            session == null &&
            (user.email ?? '').isNotEmpty) {
          return VerifyEmailScreen(
            key: ValueKey<String>('verify-${user.email}'),
            email: user.email!,
          );
        }

        return const LoginScreen();
      },
    );
  }
}
