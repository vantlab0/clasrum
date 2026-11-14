import 'package:flutter/material.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

import '../../core/widgets/clasrum_icons.dart';
import '../../main.dart';
import '../../services/auth_service.dart';
import 'auth_widgets.dart';
import 'forgot_password_screen.dart';
import 'register_screen.dart';
import 'verify_email_screen.dart';

class LoginScreen extends StatefulWidget {
  const LoginScreen({
    super.key,
    this.initialEmail,
    this.infoMessage,
  });

  final String? initialEmail;
  final String? infoMessage;

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final _formKey = GlobalKey<FormState>();
  final _emailController = TextEditingController();
  final _passwordController = TextEditingController();
  bool _obscure = true;
  bool _loading = false;
  String? _error;
  String? _info;

  AuthService? get _auth => AuthScope.maybeOf(context)?.authService;

  @override
  void initState() {
    super.initState();
    _emailController.text = widget.initialEmail ?? '';
    _info = widget.infoMessage;
  }

  @override
  void dispose() {
    _emailController.dispose();
    _passwordController.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (_loading) return;
    FocusScope.of(context).unfocus();
    setState(() {
      _error = null;
      _info = null;
    });
    if (!_formKey.currentState!.validate()) return;
    setState(() => _loading = true);
    try {
      final auth = _auth;
      if (auth == null) {
        if (!mounted) return;
        setState(() => _error = 'Authentication is unavailable. Please try again.');
        return;
      }
      final response = await auth.signInWithPassword(
        email: _emailController.text,
        password: _passwordController.text,
      );
      final user = response.user;
      if (user != null && user.emailConfirmedAt == null) {
        if (!mounted) return;
        await Navigator.of(context).pushReplacement(
          authRoute<void>(
            VerifyEmailScreen(email: _emailController.text.trim()),
          ),
        );
        return;
      }
      // AuthGate will react to auth state/session.
    } on AuthException catch (e) {
      if (!mounted) return;
      setState(() => _error = friendlyAuthError(e));
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = friendlyAuthError(e));
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  String? _validateEmail(String? value) {
    final v = (value ?? '').trim();
    if (v.isEmpty) return 'Enter your email address.';
    if (!v.contains('@') || !v.contains('.')) return 'Enter a valid email address.';
    return null;
  }

  String? _validatePassword(String? value) {
    if ((value ?? '').isEmpty) return 'Enter your password.';
    return null;
  }

  @override
  Widget build(BuildContext context) {
    return AuthScreenScaffold(
      child: Form(
        key: _formKey,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const AuthHeading(
              title: 'Welcome back',
              subtitle: 'Continue your learning journey.',
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
                  if (_info != null) ...[
                    AuthMessage(message: _info!),
                    const SizedBox(height: 14),
                  ],
                  if (_error != null) ...[
                    AuthMessage(message: _error!, isError: true),
                    const SizedBox(height: 14),
                  ],
                  AuthGlassField(
                    controller: _emailController,
                    label: 'Email',
                    hint: 'name@example.com',
                    prefixIcon: ClasrumIconKind.email,
                    keyboardType: TextInputType.emailAddress,
                    textInputAction: TextInputAction.next,
                    autofillHints: const [AutofillHints.email],
                    validator: _validateEmail,
                    enabled: !_loading,
                  ),
                  const SizedBox(height: 14),
                  AuthGlassField(
                    controller: _passwordController,
                    label: 'Password',
                    hint: 'Enter your password',
                    prefixIcon: ClasrumIconKind.lock,
                    obscureText: _obscure,
                    textInputAction: TextInputAction.done,
                    autofillHints: const [AutofillHints.password],
                    validator: _validatePassword,
                    onSubmitted: (_) => _submit(),
                    enabled: !_loading,
                    suffixIcon: IconButton(
                      onPressed: _loading
                          ? null
                          : () => setState(() => _obscure = !_obscure),
                      icon: ClasrumIcon(
                        _obscure
                            ? ClasrumIconKind.visibility
                            : ClasrumIconKind.visibilityOff,
                        size: 18,
                        color: Colors.white70,
                      ),
                      tooltip: _obscure ? 'Show password' : 'Hide password',
                    ),
                  ),
                  const SizedBox(height: 6),
                  Align(
                    alignment: Alignment.centerRight,
                    child: AuthTextLink(
                      label: 'Forgot password?',
                      subtle: true,
                      onPressed: _loading
                          ? null
                          : () {
                              Navigator.of(context).push(
                                authRoute<void>(
                                  ForgotPasswordScreen(
                                    initialEmail: _emailController.text.trim(),
                                  ),
                                ),
                              );
                            },
                    ),
                  ),
                  const SizedBox(height: 10),
                  AuthPrimaryButton(
                    label: 'Sign In',
                    onPressed: _submit,
                    loading: _loading,
                  ),
                ],
              ),
            ),
            const SizedBox(height: 18),
            Row(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                const Text(
                  'New to Clasrum? ',
                  style: TextStyle(color: Colors.white70, fontSize: 13),
                ),
                AuthTextLink(
                  label: 'Create account',
                  onPressed: _loading
                      ? null
                      : () {
                          Navigator.of(context).push(
                            authRoute<void>(
                              RegisterScreen(
                                initialEmail: _emailController.text.trim(),
                              ),
                            ),
                          );
                        },
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}
