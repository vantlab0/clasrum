import 'package:flutter/material.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

import '../../main.dart';
import '../../core/widgets/clasrum_icons.dart';
import 'auth_widgets.dart';

class ForgotPasswordScreen extends StatefulWidget {
  const ForgotPasswordScreen({super.key, this.initialEmail});

  final String? initialEmail;

  @override
  State<ForgotPasswordScreen> createState() => _ForgotPasswordScreenState();
}

class _ForgotPasswordScreenState extends State<ForgotPasswordScreen> {
  final _formKey = GlobalKey<FormState>();
  final _emailController = TextEditingController();
  bool _loading = false;
  String? _error;
  String? _success;

  @override
  void initState() {
    super.initState();
    _emailController.text = widget.initialEmail ?? '';
  }

  @override
  void dispose() {
    _emailController.dispose();
    super.dispose();
  }

  String? _validateEmail(String? value) {
    final v = (value ?? '').trim();
    if (v.isEmpty) return 'Enter your email address.';
    if (!v.contains('@') || !v.contains('.')) return 'Enter a valid email address.';
    return null;
  }

  Future<void> _submit() async {
    if (_loading) return;
    FocusScope.of(context).unfocus();
    setState(() {
      _error = null;
      _success = null;
    });
    if (!_formKey.currentState!.validate()) return;

    setState(() => _loading = true);
    try {
      final auth = AuthScope.maybeOf(context)?.authService;
      if (auth == null) {
        if (!mounted) return;
        setState(() => _error = 'Authentication is unavailable. Please try again.');
      } else {
        await auth.resetPassword(email: _emailController.text);
        if (!mounted) return;
        setState(() {
          _success =
              'If an account exists for this email, a password reset link has been sent.';
        });
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
      child: Form(
        key: _formKey,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const AuthHeading(
              title: 'Reset password',
              subtitle: 'We’ll send a link to your email.',
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
                  if (_success != null) ...[
                    AuthMessage(message: _success!),
                    const SizedBox(height: 14),
                  ],
                  AuthGlassField(
                    controller: _emailController,
                    label: 'Email',
                    hint: 'name@example.com',
                    prefixIcon: ClasrumIconKind.email,
                    keyboardType: TextInputType.emailAddress,
                    textInputAction: TextInputAction.done,
                    autofillHints: const [AutofillHints.email],
                    validator: _validateEmail,
                    enabled: !_loading,
                    onSubmitted: (_) => _submit(),
                  ),
                  const SizedBox(height: 18),
                  AuthPrimaryButton(
                    label: 'Send Reset Link',
                    onPressed: _submit,
                    loading: _loading,
                  ),
                ],
              ),
            ),
            const SizedBox(height: 18),
            Center(
              child: AuthTextLink(
                label: 'Back to login',
                subtle: true,
                onPressed: _loading ? null : () => Navigator.of(context).maybePop(),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
