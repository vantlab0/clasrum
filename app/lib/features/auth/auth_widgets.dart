import 'package:flutter/material.dart';

import '../../core/theme.dart';
import '../../core/widgets/clasrum_icons.dart';

/// Smooth, premium route transition for auth flow (slowed for visibility).
PageRoute<T> authRoute<T>(Widget page) {
  return PageRouteBuilder<T>(
    transitionDuration: const Duration(milliseconds: 700),
    reverseTransitionDuration: const Duration(milliseconds: 520),
    pageBuilder: (context, animation, secondaryAnimation) => page,
    transitionsBuilder: (context, animation, secondaryAnimation, child) {
      final curved = CurvedAnimation(
        parent: animation,
        curve: Curves.easeInOutCubic,
        reverseCurve: Curves.easeInOutCubic,
      );
      return FadeTransition(
        opacity: curved,
        child: SlideTransition(
          position: Tween<Offset>(
            begin: const Offset(0, 0.05),
            end: Offset.zero,
          ).animate(curved),
          child: ScaleTransition(
            scale: Tween<double>(begin: 0.985, end: 1.0).animate(curved),
            child: child,
          ),
        ),
      );
    },
  );
}

/// Shared auth layout (pure black background, no gradients or heavy shadows).
class AuthScreenScaffold extends StatelessWidget {
  const AuthScreenScaffold({
    super.key,
    required this.child,
    this.showBack = false,
    this.onBack,
  });

  final Widget child;
  final bool showBack;
  final VoidCallback? onBack;

  @override
  Widget build(BuildContext context) {
    final mq = MediaQuery.of(context);
    final bottomInset = mq.viewInsets.bottom;
    return Scaffold(
      backgroundColor: Colors.black,
      resizeToAvoidBottomInset: true,
      body: SafeArea(
        child: LayoutBuilder(
          builder: (context, constraints) {
            return AnimatedPadding(
              duration: const Duration(milliseconds: 300),
              curve: Curves.easeOutCubic,
              padding: EdgeInsets.only(bottom: bottomInset > 0 ? 8 : 0),
              child: SingleChildScrollView(
                keyboardDismissBehavior:
                    ScrollViewKeyboardDismissBehavior.onDrag,
                padding: EdgeInsets.fromLTRB(
                  20,
                  showBack ? 8 : 20,
                  20,
                  20,
                ),
                child: ConstrainedBox(
                  constraints: BoxConstraints(
                    minHeight: constraints.maxHeight - (showBack ? 28 : 40),
                  ),
                  child: Center(
                    child: ConstrainedBox(
                      constraints: const BoxConstraints(maxWidth: 420),
                      child: Column(
                        mainAxisAlignment: MainAxisAlignment.center,
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: [
                          if (showBack) ...[
                            Align(
                              alignment: Alignment.centerLeft,
                              child: IconButton(
                                icon: const ClasrumIcon(
                                  ClasrumIconKind.back,
                                  size: 20,
                                  color: Colors.white,
                                ),
                                tooltip: 'Back',
                                onPressed:
                                    onBack ?? () => Navigator.maybePop(context),
                                style: IconButton.styleFrom(
                                  backgroundColor: Colors.black,
                                  side: const BorderSide(color: Colors.white24),
                                  shape: const CircleBorder(),
                                ),
                              ),
                            ),
                            const SizedBox(height: 10),
                          ],
                          const _AuthBrandMark(),
                          const SizedBox(height: 24),
                          child,
                        ],
                      ),
                    ),
                  ),
                ),
              ),
            );
          },
        ),
      ),
    );
  }
}

class _AuthBrandMark extends StatelessWidget {
  const _AuthBrandMark();

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        Container(
          width: 56,
          height: 56,
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(18),
            color: Colors.black,
            border: Border.all(color: Colors.white24, width: 1),
          ),
          child: Center(
            child: CustomPaint(
              size: const Size(24, 24),
              painter: _ClasrumMarkPainter(),
            ),
          ),
        ),
        const SizedBox(height: 12),
        const Text(
          'Clasrum',
          style: TextStyle(
            color: Colors.white,
            fontSize: 15,
            fontWeight: FontWeight.w600,
            letterSpacing: 0.6,
          ),
        ),
      ],
    );
  }
}

/// Minimal custom brand glyph (no external assets, refined geometric mark).
class _ClasrumMarkPainter extends CustomPainter {
  @override
  void paint(Canvas canvas, Size size) {
    final stroke = Paint()
      ..color = AppColors.white
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.6
      ..strokeCap = StrokeCap.round
      ..strokeJoin = StrokeJoin.round;
    final accent = Paint()
      ..color = AppColors.blue
      ..style = PaintingStyle.fill;

    final w = size.width;
    final h = size.height;
    // Stylized "C" arc frame
    final rect = Rect.fromLTWH(2, 2, w - 4, h - 4);
    canvas.drawArc(rect, -2.35, 4.2, false, stroke);
    // Inner learning node
    canvas.drawCircle(Offset(w * 0.56, h * 0.5), 2.4, accent);
    // Subtle stem
    canvas.drawLine(
      Offset(w * 0.56, h * 0.5),
      Offset(w * 0.82, h * 0.36),
      stroke..color = AppColors.white.withValues(alpha: 0.85),
    );
  }

  @override
  bool shouldRepaint(covariant CustomPainter oldDelegate) => false;
}

class AuthHeading extends StatelessWidget {
  const AuthHeading({
    super.key,
    required this.title,
    required this.subtitle,
  });

  final String title;
  final String subtitle;

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        Text(
          title,
          textAlign: TextAlign.center,
          style: const TextStyle(
            color: AppColors.white,
            fontSize: 28,
            fontWeight: FontWeight.w600,
            height: 1.2,
            letterSpacing: -0.3,
          ),
        ),
        const SizedBox(height: 8),
        Text(
          subtitle,
          textAlign: TextAlign.center,
          style: const TextStyle(
            color: AppColors.muted,
            fontSize: 14,
            height: 1.5,
          ),
        ),
      ],
    );
  }
}

class AuthGlassField extends StatelessWidget {
  const AuthGlassField({
    super.key,
    required this.controller,
    required this.label,
    required this.hint,
    this.keyboardType,
    this.textInputAction,
    this.obscureText = false,
    this.onSubmitted,
    this.validator,
    this.autofillHints,
    this.suffixIcon,
    this.prefixIcon,
    this.enabled = true,
  });

  final TextEditingController controller;
  final String label;
  final String hint;
  final TextInputType? keyboardType;
  final TextInputAction? textInputAction;
  final bool obscureText;
  final ValueChanged<String>? onSubmitted;
  final String? Function(String?)? validator;
  final Iterable<String>? autofillHints;
  final Widget? suffixIcon;
  final ClasrumIconKind? prefixIcon;
  final bool enabled;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          label,
          style: const TextStyle(
            color: Colors.white70,
            fontSize: 12,
            fontWeight: FontWeight.w500,
          ),
        ),
        const SizedBox(height: 6),
        TextFormField(
          controller: controller,
          keyboardType: keyboardType,
          textInputAction: textInputAction,
          obscureText: obscureText,
          onFieldSubmitted: onSubmitted,
          validator: validator,
          autofillHints: autofillHints,
          enabled: enabled,
          style: const TextStyle(color: Colors.white, fontSize: 15),
          cursorColor: Colors.white,
          decoration: InputDecoration(
            hintText: hint,
            filled: true,
            fillColor: Colors.black,
            hintStyle: const TextStyle(color: Colors.white38),
            prefixIcon: prefixIcon == null
                ? null
                : Padding(
                    padding: const EdgeInsetsDirectional.only(start: 12, end: 8),
                    child: ClasrumIcon(
                      prefixIcon!,
                      size: 18,
                      color: Colors.white70,
                    ),
                  ),
            prefixIconConstraints:
                const BoxConstraints(minWidth: 38, minHeight: 18),
            suffixIcon: suffixIcon,
            enabledBorder: OutlineInputBorder(
              borderRadius: BorderRadius.circular(12),
              borderSide: const BorderSide(color: Colors.white24),
            ),
            focusedBorder: OutlineInputBorder(
              borderRadius: BorderRadius.circular(12),
              borderSide: const BorderSide(color: Colors.white54),
            ),
            errorBorder: OutlineInputBorder(
              borderRadius: BorderRadius.circular(12),
              borderSide: const BorderSide(color: Colors.white38),
            ),
            focusedErrorBorder: OutlineInputBorder(
              borderRadius: BorderRadius.circular(12),
              borderSide: const BorderSide(color: Colors.white54),
            ),
            disabledBorder: OutlineInputBorder(
              borderRadius: BorderRadius.circular(12),
              borderSide: const BorderSide(color: Colors.white12),
            ),
          ),
        ),
      ],
    );
  }
}

class AuthMessage extends StatelessWidget {
  const AuthMessage({
    super.key,
    required this.message,
    this.isError = false,
  });

  final String message;
  final bool isError;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      decoration: BoxDecoration(
        color: Colors.black,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: Colors.white24),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          ClasrumIcon(
            isError ? ClasrumIconKind.info : ClasrumIconKind.resume,
            size: 16,
            color: Colors.white70,
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              message,
              style: const TextStyle(color: Colors.white70, fontSize: 13, height: 1.45),
            ),
          ),
        ],
      ),
    );
  }
}

class AuthPrimaryButton extends StatelessWidget {
  const AuthPrimaryButton({
    super.key,
    required this.label,
    required this.onPressed,
    this.loading = false,
  });

  final String label;
  final VoidCallback? onPressed;
  final bool loading;

  @override
  Widget build(BuildContext context) {
    return FilledButton(
      onPressed: loading ? null : onPressed,
      style: FilledButton.styleFrom(
        backgroundColor: AppColors.blue,
        foregroundColor: Colors.white,
        disabledBackgroundColor: AppColors.blue.withValues(alpha: 0.75),
        disabledForegroundColor: Colors.white,
        minimumSize: const Size.fromHeight(48),
        elevation: 0,
        shadowColor: Colors.transparent,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
      ),
      child: loading
          ? Row(
              key: const ValueKey<String>('loading'),
              mainAxisAlignment: MainAxisAlignment.center,
              mainAxisSize: MainAxisSize.min,
              children: const <Widget>[
                SizedBox(
                  width: 18,
                  height: 18,
                  child: CircularProgressIndicator(
                    strokeWidth: 2.2,
                    color: Colors.white,
                  ),
                ),
                SizedBox(width: 10),
                Text(
                  'Please wait…',
                  style: TextStyle(fontSize: 14, fontWeight: FontWeight.w600),
                ),
              ],
            )
          : Text(
              label,
              key: ValueKey<String>('label-$label'),
              style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w600),
            ),
    );
  }
}

class AuthTextLink extends StatelessWidget {
  const AuthTextLink({
    super.key,
    required this.label,
    required this.onPressed,
    this.subtle = false,
  });

  final String label;
  final VoidCallback? onPressed;
  final bool subtle;

  @override
  Widget build(BuildContext context) {
    final color = subtle ? Colors.white70 : Colors.white;
    return TextButton(
      onPressed: onPressed,
      style: TextButton.styleFrom(
        foregroundColor: color,
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
        minimumSize: const Size(0, 36),
        tapTargetSize: MaterialTapTargetSize.shrinkWrap,
      ),
      child: Text(
        label,
        style: TextStyle(
          fontSize: 13,
          fontWeight: subtle ? FontWeight.w500 : FontWeight.w600,
          color: color,
        ),
      ),
    );
  }
}

String friendlyAuthError(Object error) {
  final raw = error.toString();
  final msg = raw.toLowerCase();
  if (msg.contains('invalid login credentials') ||
      msg.contains('invalid email or password')) {
    return 'Invalid email or password. Please try again.';
  }
  if (msg.contains('email not confirmed') ||
      msg.contains('email_not_confirmed')) {
    return 'Please verify your email before signing in.';
  }
  if (msg.contains('user already registered') ||
      msg.contains('already registered') ||
      msg.contains('already exists')) {
    return 'An account with this email already exists. Try signing in instead.';
  }
  if (msg.contains('password should be') ||
      msg.contains('weak password') ||
      msg.contains('at least')) {
    return 'Please choose a stronger password (at least 8 characters).';
  }
  if (msg.contains('invalid email')) {
    return 'Please enter a valid email address.';
  }
  if (msg.contains('network') ||
      msg.contains('socket') ||
      msg.contains('failed host lookup') ||
      msg.contains('timeout') ||
      msg.contains('connection')) {
    return 'Network error. Check your connection and try again.';
  }
  if (msg.contains('rate limit') || msg.contains('too many')) {
    return 'Too many attempts. Please wait a moment and try again.';
  }
  if (msg.contains('expired') || msg.contains('otp_expired')) {
    return 'This link or code has expired. Please request a new one.';
  }
  // Supabase AuthException often has message property via toString; keep it readable.
  final cleaned = raw
      .replaceFirst(RegExp(r'^AuthException\([^)]*\):\s*'), '')
      .replaceFirst(RegExp(r'^Exception:\s*'), '')
      .trim();
  if (cleaned.isEmpty || cleaned.length > 180) {
    return 'Something went wrong. Please try again.';
  }
  return cleaned;
}
