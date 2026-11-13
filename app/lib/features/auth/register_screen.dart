import 'package:flutter/material.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

import '../../core/theme.dart';
import '../../core/widgets/clasrum_icons.dart';
import '../../main.dart';
import 'auth_widgets.dart';
import 'login_screen.dart';
import 'verify_email_screen.dart';

class RegisterScreen extends StatefulWidget {
  const RegisterScreen({super.key, this.initialEmail});

  final String? initialEmail;

  @override
  State<RegisterScreen> createState() => _RegisterScreenState();
}

class _RegisterScreenState extends State<RegisterScreen> {
  final _formKey = GlobalKey<FormState>();
  final _nameController = TextEditingController();
  final _emailController = TextEditingController();
  final _passwordController = TextEditingController();
  final _confirmController = TextEditingController();
  bool _obscurePassword = true;
  bool _obscureConfirm = true;
  bool _loading = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _emailController.text = widget.initialEmail ?? '';
  }

  @override
  void dispose() {
    _nameController.dispose();
    _emailController.dispose();
    _passwordController.dispose();
    _confirmController.dispose();
    super.dispose();
  }

  String? _validateName(String? value) {
    final v = (value ?? '').trim();
    if (v.isEmpty) return 'Enter your full name.';
    if (v.length < 2) return 'Name is too short.';
    return null;
  }

  String? _validateEmail(String? value) {
    final v = (value ?? '').trim();
    if (v.isEmpty) return 'Enter your email address.';
    if (!v.contains('@') || !v.contains('.')) return 'Enter a valid email address.';
    return null;
  }

  String? _validatePassword(String? value) {
    final v = value ?? '';
    if (v.isEmpty) return 'Create a password.';
    if (v.length < 8) return 'Use at least 8 characters.';
    return null;
  }

  String? _validateConfirm(String? value) {
    if ((value ?? '').isEmpty) return 'Confirm your password.';
    if (value != _passwordController.text) return 'Passwords do not match.';
    return null;
  }

  Future<void> _submit() async {
    if (_loading) return;
    FocusScope.of(context).unfocus();
    setState(() => _error = null);
    if (!_formKey.currentState!.validate()) return;

    final scope = AuthScope.maybeOf(context);
    if (scope == null) {
      if (mounted) {
        setState(() => _error = 'Authentication is unavailable. Please try again.');
      }
      return;
    }
    final auth = scope.authService;
    final profileService = scope.profileService;
    final email = _emailController.text.trim();
    final name = _nameController.text.trim();

    setState(() => _loading = true);
    try {
      final response = await auth.signUpWithPassword(
        email: email,
        password: _passwordController.text,
        displayName: name,
      );

      // If a session exists (e.g. confirmations disabled), ensure profile and proceed.
      if (response.session != null && response.user != null) {
        try {
          await profileService.ensureCurrentProfile(displayName: name);
        } catch (_) {
          // Non-fatal: DB trigger or later sync can create profile; auth state will update.
        }
      }

      if (!mounted) return;
      final needsVerification =
          response.session == null || response.user?.emailConfirmedAt == null;
      if (needsVerification) {
        Navigator.of(context).pushAndRemoveUntil(
          authRoute<void>(VerifyEmailScreen(email: email)),
          (route) => route.isFirst,
        );
      } else {
        Navigator.of(context).popUntil((route) => route.isFirst);
      }
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

  @override
  Widget build(BuildContext context) {
    return AuthScreenScaffold(
      showBack: true,
      onBack: () => Navigator.of(context).maybePop(),
      child: Form(
        key: _formKey,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const AuthHeading(
              title: 'Create your account',
              subtitle: 'Start a more personal way to learn.',
            ),
            const SizedBox(height: 24),
            GlassContainer(
              radius: 22,
              padding: const EdgeInsets.all(18),
              blurSigma: 12,
              backgroundOpacity: 0.38,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  if (_error != null) ...[
                    AuthMessage(message: _error!, isError: true),
                    const SizedBox(height: 14),
                  ],
                  AuthGlassField(
                    controller: _nameController,
                    label: 'Full name',
                    hint: 'Your name',
                    prefixIcon: ClasrumIconKind.profile,
                    textInputAction: TextInputAction.next,
                    autofillHints: const [AutofillHints.name],
                    validator: _validateName,
                    enabled: !_loading,
                  ),
                  const SizedBox(height: 14),
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
                    hint: 'At least 8 characters',
                    prefixIcon: ClasrumIconKind.lock,
                    obscureText: _obscurePassword,
                    textInputAction: TextInputAction.next,
                    autofillHints: const [AutofillHints.newPassword],
                    validator: _validatePassword,
                    enabled: !_loading,
                    suffixIcon: IconButton(
                      onPressed: _loading
                          ? null
                          : () => setState(
                              () => _obscurePassword = !_obscurePassword),
                      icon: ClasrumIcon(
                        _obscurePassword
                            ? ClasrumIconKind.visibility
                            : ClasrumIconKind.visibilityOff,
                        size: 18,
                        color: AppColors.muted,
                      ),
                      tooltip:
                          _obscurePassword ? 'Show password' : 'Hide password',
                    ),
                  ),
                  const SizedBox(height: 14),
                  AuthGlassField(
                    controller: _confirmController,
                    label: 'Confirm password',
                    hint: 'Re-enter your password',
                    prefixIcon: ClasrumIconKind.lock,
                    obscureText: _obscureConfirm,
                    textInputAction: TextInputAction.done,
                    autofillHints: const [AutofillHints.newPassword],
                    validator: _validateConfirm,
                    onSubmitted: (_) => _submit(),
                    enabled: !_loading,
                    suffixIcon: IconButton(
                      onPressed: _loading
                          ? null
                          : () => setState(
                              () => _obscureConfirm = !_obscureConfirm),
                      icon: ClasrumIcon(
                        _obscureConfirm
                            ? ClasrumIconKind.visibility
                            : ClasrumIconKind.visibilityOff,
                        size: 18,
                        color: AppColors.muted,
                      ),
                      tooltip:
                          _obscureConfirm ? 'Show password' : 'Hide password',
                    ),
                  ),
                  const SizedBox(height: 18),
                  AuthPrimaryButton(
                    label: 'Create Account',
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
                  'Already have an account? ',
                  style: TextStyle(color: AppColors.muted, fontSize: 13),
                ),
                AuthTextLink(
                  label: 'Sign in',
                  onPressed: _loading
                      ? null
                      : () {
                          Navigator.of(context).pushReplacement(
                            authRoute<void>(
                              LoginScreen(
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
