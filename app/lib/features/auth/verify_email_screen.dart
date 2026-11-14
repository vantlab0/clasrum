import 'dart:async';

import 'package:flutter/material.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

import '../../main.dart';
import 'auth_widgets.dart';
import 'login_screen.dart';

class VerifyEmailScreen extends StatefulWidget {
  const VerifyEmailScreen({super.key, required this.email});

  final String email;

  @override
  State<VerifyEmailScreen> createState() => _VerifyEmailScreenState();
}

class _VerifyEmailScreenState extends State<VerifyEmailScreen> {
  bool _resending = false;
  bool _checking = false;
  String? _error;
  String? _info;
  int _cooldownSeconds = 0;
  Timer? _cooldownTimer;

  @override
  void dispose() {
    _cooldownTimer?.cancel();
    super.dispose();
  }

  void _startCooldown([int seconds = 30]) {
    _cooldownTimer?.cancel();
    setState(() => _cooldownSeconds = seconds);
    _cooldownTimer = Timer.periodic(const Duration(seconds: 1), (timer) {
      if (!mounted) {
        timer.cancel();
        return;
      }
      if (_cooldownSeconds <= 1) {
        timer.cancel();
        setState(() => _cooldownSeconds = 0);
      } else {
        setState(() => _cooldownSeconds -= 1);
      }
    });
  }

  Future<void> _resend() async {
    if (_resending || _cooldownSeconds > 0) return;
    setState(() {
      _resending = true;
      _error = null;
      _info = null;
    });
    try {
      final auth = AuthScope.maybeOf(context)?.authService;
      if (auth == null) {
        if (!mounted) return;
        setState(() => _error = 'Authentication is unavailable. Please try again.');
        return;
      }
      await auth.resendSignupVerificationEmail(email: widget.email);
      if (!mounted) return;
      setState(() {
        _info = 'Verification email sent. Please check your inbox and spam folder.';
      });
      _startCooldown(30);
    } on AuthException catch (e) {
      if (!mounted) return;
      setState(() => _error = friendlyAuthError(e));
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = friendlyAuthError(e));
    } finally {
      if (mounted) setState(() => _resending = false);
    }
  }

  Future<void> _checkStatus() async {
    if (_checking) return;
    setState(() {
      _checking = true;
      _error = null;
      _info = null;
    });
    try {
      final client = Supabase.instance.client;
      final response = await client.auth.getUser();
      final user = response.user ?? client.auth.currentUser;
      await client.auth.refreshSession();
      final refreshedUser = client.auth.currentUser ?? user;
      final confirmed = refreshedUser?.emailConfirmedAt != null;
      if (!mounted) return;
      if (confirmed && client.auth.currentSession != null) {
        setState(() => _info = 'Email verified. Continuing...');
        // AuthGate will react to auth state/session.
        return;
      }
      if (confirmed) {
        setState(() {
          _info =
              'Email verified. Please sign in to continue.';
        });
      } else {
        setState(() {
          _info =
              'Email not verified yet. Open the link in your email, then check again.';
        });
      }
    } on AuthException catch (e) {
      if (!mounted) return;
      setState(() => _error = friendlyAuthError(e));
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = friendlyAuthError(e));
    } finally {
      if (mounted) setState(() => _checking = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return AuthScreenScaffold(
      showBack: false,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const AuthHeading(
            title: 'Verify your email',
            subtitle:
                'We sent a verification link to your email address. Confirm it to continue.',
          ),
          const SizedBox(height: 24),
          Container(
            padding: const EdgeInsets.all(18),
            decoration: BoxDecoration(
              color: Colors.black,
              borderRadius: BorderRadius.circular(22),
              border: Border.all(color: Colors.white24),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                const Text(
                  'Email address',
                  style: TextStyle(
                    color: Colors.white70,
                    fontSize: 12,
                    fontWeight: FontWeight.w500,
                  ),
                ),
                const SizedBox(height: 6),
                Container(
                  width: double.infinity,
                  padding:
                      const EdgeInsets.symmetric(horizontal: 14, vertical: 14),
                  decoration: BoxDecoration(
                    color: Colors.black,
                    borderRadius: BorderRadius.circular(14),
                    border: Border.all(color: Colors.white24),
                  ),
                  child: Text(
                    widget.email,
                    style: const TextStyle(
                      color: Colors.white,
                      fontSize: 15,
                      fontWeight: FontWeight.w500,
                    ),
                  ),
                ),
                if (_error != null) ...[
                  const SizedBox(height: 14),
                  AuthMessage(message: _error!, isError: true),
                ],
                if (_info != null) ...[
                  const SizedBox(height: 14),
                  AuthMessage(message: _info!),
                ],
                const SizedBox(height: 18),
                AuthPrimaryButton(
                  label: _checking ? 'Checking...' : 'I\'ve verified, check status',
                  onPressed: _checkStatus,
                  loading: _checking,
                ),
                const SizedBox(height: 10),
                OutlinedButton(
                  onPressed:
                      (_resending || _cooldownSeconds > 0) ? null : _resend,
                  style: OutlinedButton.styleFrom(
                    foregroundColor: Colors.white,
                    disabledForegroundColor: Colors.white54,
                    minimumSize: const Size.fromHeight(46),
                    side: const BorderSide(color: Colors.white24),
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(14),
                    ),
                  ),
                  child: _resending
                      ? const Row(
                          key: ValueKey<String>('resending'),
                          mainAxisAlignment: MainAxisAlignment.center,
                          mainAxisSize: MainAxisSize.min,
                          children: <Widget>[
                            SizedBox(
                              width: 16,
                              height: 16,
                              child: CircularProgressIndicator(
                                strokeWidth: 2,
                                color: Colors.white,
                              ),
                            ),
                            SizedBox(width: 10),
                            Text(
                              'Sending…',
                              style: TextStyle(
                                fontSize: 13,
                                fontWeight: FontWeight.w500,
                                color: Colors.white,
                              ),
                            ),
                          ],
                        )
                      : Text(
                          _cooldownSeconds > 0
                              ? 'Resend email in ${_cooldownSeconds}s'
                              : 'Resend verification email',
                          key: ValueKey<String>('resend-${_cooldownSeconds > 0}'),
                          style: const TextStyle(
                            fontSize: 13,
                            fontWeight: FontWeight.w500,
                          ),
                        ),
                ),
              ],
            ),
          ),
          const SizedBox(height: 18),
          Center(
            child: AuthTextLink(
              label: 'Return to login',
              subtle: true,
              onPressed: (_resending || _checking)
                  ? null
                  : () {
                      Navigator.of(context).pushAndRemoveUntil(
                        authRoute<void>(
                          LoginScreen(initialEmail: widget.email),
                        ),
                        (route) => false,
                      );
                    },
            ),
          ),
        ],
      ),
    );
  }
}
