import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

import '../core/storage.dart';
import '../core/supabase_bootstrap.dart';

/// Centralized authentication service backed by Supabase.
///
/// Responsibilities:
/// - sign up / sign in / sign out / password reset
/// - expose current user and auth state changes
/// - keep backend-compatible bearer token storage in sync with Supabase sessions
class AuthService extends ChangeNotifier {
  AuthService({required this.storage}) {
    _attachListener();
    unawaited(_syncTokenFromCurrentSession());
  }

  final AppStorage storage;
  StreamSubscription<AuthState>? _authSubscription;

  SupabaseClient get _client {
    final client = SupabaseBootstrap.clientOrNull;
    if (client == null) {
      throw const AuthServiceException(
        'Supabase is not configured or failed to initialize.',
      );
    }
    return client;
  }

  GoTrueClient get _auth => _client.auth;

  /// Current authenticated Supabase user, if any.
  User? get currentUser => SupabaseBootstrap.clientOrNull?.auth.currentUser;

  /// Current Supabase session, if any.
  Session? get currentSession => SupabaseBootstrap.clientOrNull?.auth.currentSession;

  /// Stream of Supabase auth state changes.
  Stream<AuthState> get authStateChanges {
    final client = SupabaseBootstrap.clientOrNull;
    if (client == null) return const Stream<AuthState>.empty();
    return client.auth.onAuthStateChange;
  }

  bool get isSignedIn => currentSession != null && currentUser != null;

  Future<AuthResponse> signUpWithPassword({
    required String email,
    required String password,
    String? displayName,
    String? emailRedirectTo,
  }) async {
    final response = await _auth.signUp(
      email: email.trim(),
      password: password,
      data: <String, dynamic>{
        if (displayName != null && displayName.trim().isNotEmpty)
          'display_name': displayName.trim(),
      },
      emailRedirectTo: emailRedirectTo,
    );
    await _syncToken(response.session);
    notifyListeners();
    return response;
  }

  Future<AuthResponse> signInWithPassword({
    required String email,
    required String password,
  }) async {
    final response = await _auth.signInWithPassword(
      email: email.trim(),
      password: password,
    );
    await _syncToken(response.session);
    notifyListeners();
    return response;
  }

  Future<void> signOut() async {
    try {
      if (SupabaseBootstrap.clientOrNull != null) {
        await _auth.signOut();
      }
    } catch (_) {
      // Always clear local/session state even if remote sign-out fails.
    } finally {
      await storage.clearUserScopedData();
      notifyListeners();
    }
  }

  /// Handles backend auth failures (401/403 from our API).
  ///
  /// If a Supabase session is active, the app-level session is still valid and
  /// we must not sign the user out (backend token validation/config issues
  /// should not bounce the user back to login). If no Supabase session exists,
  /// force local sign-out and clear user-scoped data.
  Future<void> signOutExpiredSession() async {
    final hasSupabaseSession = currentSession != null;
    if (hasSupabaseSession) {
      notifyListeners();
      return;
    }
    try {
      if (SupabaseBootstrap.clientOrNull != null) {
        await _auth.signOut(scope: SignOutScope.local);
      }
    } catch (_) {
      // Best effort; local cleanup below is authoritative for app state.
    } finally {
      await storage.clearUserScopedData();
      notifyListeners();
    }
  }

  /// Sends a password reset email. Configure redirect URL in Supabase dashboard
  /// / app deep-link handling as needed for your platform.
  Future<void> resetPassword({
    required String email,
    String? redirectTo,
  }) async {
    await _auth.resetPasswordForEmail(
      email.trim(),
      redirectTo: redirectTo,
    );
  }

  /// Requests Supabase to resend a signup confirmation / verification email.
  Future<void> resendSignupVerificationEmail({
    required String email,
    String? emailRedirectTo,
  }) async {
    await _auth.resend(
      type: OtpType.signup,
      email: email.trim(),
      emailRedirectTo: emailRedirectTo,
    );
  }

  /// Exchanges an email verification / recovery / magic-link code for a session
  /// when your app receives it via deep link or manual entry.
  Future<AuthResponse> verifyOtp({
    required String email,
    required String token,
    required OtpType type,
  }) async {
    final response = await _auth.verifyOTP(
      email: email.trim(),
      token: token,
      type: type,
    );
    await _syncToken(response.session);
    notifyListeners();
    return response;
  }

  /// Restores/persists session awareness on app startup.
  ///
  /// supabase_flutter persists sessions internally; this ensures the backend
  /// bearer token mirror is up to date.
  Future<void> restoreSession() async {
    await _syncTokenFromCurrentSession();
    notifyListeners();
  }

  /// Access token suitable for forwarding to the existing backend/WS, if any.
  String? get accessToken => currentSession?.accessToken ?? storage.token;

  void _attachListener() {
    final client = SupabaseBootstrap.clientOrNull;
    if (client == null) return;
    _authSubscription = client.auth.onAuthStateChange.listen((state) async {
      await _syncToken(state.session);
      notifyListeners();
    });
  }

  Future<void> _syncTokenFromCurrentSession() async {
    await _syncToken(currentSession);
  }

  Future<void> _syncToken(Session? session) async {
    final token = session?.accessToken;
    if (token != null && token.isNotEmpty) {
      await storage.setToken(token);
    } else {
      await storage.clearAuthToken();
    }
  }

  @override
  void dispose() {
    unawaited(_authSubscription?.cancel());
    super.dispose();
  }
}

class AuthServiceException implements Exception {
  const AuthServiceException(this.message);
  final String message;

  @override
  String toString() => message;
}
