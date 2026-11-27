import 'dart:ui';

import 'package:flutter/material.dart';

import '../theme.dart';
import 'pwony_icons.dart';

/// Heavy frosted-glass surface: strong blur, layered frost and a hairline edge.
class GlassContainer extends StatelessWidget {
  const GlassContainer({
    super.key,
    required this.child,
    this.padding,
    this.margin,
    this.radius = AppRadius.lg,
    this.borderOpacity = 0.65,
    this.blurSigma = 22,
    this.backgroundOpacity = 0.38,
    this.borderColor,
    this.color,
  });

  final Widget child;
  final EdgeInsetsGeometry? padding;
  final EdgeInsetsGeometry? margin;
  final double radius;
  final double borderOpacity;
  final double blurSigma;
  final double backgroundOpacity;
  final Color? borderColor;
  final Color? color;

  @override
  Widget build(BuildContext context) {
    final base = color ?? AppColors.surface;
    final border = borderColor ?? AppColors.border;
    // Keep every surface genuinely frosted even when a call site asks lighter.
    final fill = backgroundOpacity.clamp(0.18, 0.62);
    final effectiveBlur = blurSigma > 0 && blurSigma < 18 ? 18.0 : blurSigma;
    Widget content = Container(
      padding: padding,
      decoration: BoxDecoration(
        gradient: LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: <Color>[
            AppColors.glassTint.withValues(alpha: 0.08 + fill * 0.24),
            base.withValues(alpha: fill),
            base.withValues(alpha: (fill * 0.72).clamp(0.0, 1.0)),
          ],
          stops: const <double>[0, 0.35, 1],
        ),
        borderRadius: BorderRadius.circular(radius),
        border: Border.all(
          color: border.withValues(alpha: borderOpacity),
          width: 1.2,
        ),
        boxShadow: <BoxShadow>[
          BoxShadow(
            color: AppColors.accent.withValues(alpha: 0.55),
            blurRadius: 28,
            offset: const Offset(0, 10),
          ),
        ],
      ),
      child: child,
    );
    if (effectiveBlur > 0) {
      content = ClipRRect(
        borderRadius: BorderRadius.circular(radius),
        child: BackdropFilter(
          filter: ImageFilter.blur(
            sigmaX: effectiveBlur,
            sigmaY: effectiveBlur,
          ),
          child: content,
        ),
      );
    } else {
      content = ClipRRect(
        borderRadius: BorderRadius.circular(radius),
        child: content,
      );
    }
    if (margin != null) {
      return Padding(padding: margin!, child: content);
    }
    return content;
  }
}

/// Circular/squircle glass icon button for compact actions.
class GlassIconButton extends StatelessWidget {
  const GlassIconButton({
    super.key,
    required this.icon,
    required this.onPressed,
    this.size = 44,
    this.iconSize = 20,
    this.tooltip,
    this.backgroundOpacity = 0.42,
    this.borderOpacity = 0.6,
    this.blurSigma = 20,
    this.iconColor = AppColors.white,
  });

  final PwonyIconKind icon;
  final VoidCallback? onPressed;
  final double size;
  final double iconSize;
  final String? tooltip;
  final double backgroundOpacity;
  final double borderOpacity;
  final double blurSigma;
  final Color iconColor;

  @override
  Widget build(BuildContext context) {
    final button = SizedBox(
      width: size,
      height: size,
      child: GlassContainer(
        radius: size / 2,
        padding: EdgeInsets.zero,
        blurSigma: blurSigma,
        backgroundOpacity: backgroundOpacity,
        borderOpacity: borderOpacity,
        child: Material(
          type: MaterialType.transparency,
          child: InkWell(
            onTap: onPressed,
            customBorder: const CircleBorder(),
            child: Center(
              child: PwonyIcon(icon, size: iconSize, color: iconColor),
            ),
          ),
        ),
      ),
    );
    if (tooltip == null || tooltip!.isEmpty) return button;
    return Tooltip(message: tooltip!, child: button);
  }
}

/// Compact glass-styled modal/dialog surface.
class GlassModal extends StatelessWidget {
  const GlassModal({
    super.key,
    required this.child,
    this.padding = const EdgeInsets.all(AppSpacing.xl),
    this.radius = AppRadius.xl,
  });

  final Widget child;
  final EdgeInsetsGeometry padding;
  final double radius;

  @override
  Widget build(BuildContext context) {
    return Dialog(
      backgroundColor: Colors.transparent,
      elevation: 0,
      insetPadding: const EdgeInsets.symmetric(horizontal: 20, vertical: 24),
      child: GlassContainer(
        radius: radius,
        padding: padding,
        blurSigma: 28,
        backgroundOpacity: 0.62,
        borderOpacity: 0.7,
        child: child,
      ),
    );
  }

  static Future<T?> show<T>(BuildContext context, {required Widget child}) {
    return showDialog<T>(
      context: context,
      barrierColor: Colors.black.withValues(alpha: 0.55),
      builder: (_) => GlassModal(child: child),
    );
  }
}

/// Primary action button — pure black glass with a hairline frost edge.
class PrimaryActionButton extends StatelessWidget {
  const PrimaryActionButton({
    super.key,
    required this.onPressed,
    required this.label,
    this.icon,
    this.compact = false,
  });

  final VoidCallback? onPressed;
  final String label;
  final PwonyIconKind? icon;
  final bool compact;

  @override
  Widget build(BuildContext context) {
    final style = FilledButton.styleFrom(
      backgroundColor: AppColors.accent,
      foregroundColor: AppColors.white,
      side: BorderSide(color: AppColors.glassTint.withValues(alpha: 0.22)),
      minimumSize: Size(compact ? 0 : 64, compact ? 40 : 48),
      padding: EdgeInsets.symmetric(
        horizontal: compact ? 14 : 20,
        vertical: compact ? 10 : 14,
      ),
      textStyle: TextStyle(
        fontSize: compact ? 13 : 14,
        fontWeight: FontWeight.w600,
      ),
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(compact ? 12 : AppRadius.md),
      ),
    );
    if (icon != null) {
      return FilledButton(
        onPressed: onPressed,
        style: style,
        child: Row(
          mainAxisSize: MainAxisSize.min,
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            PwonyIcon(icon!, size: compact ? 16 : 18, color: AppColors.white),
            const SizedBox(width: 8),
            Text(label),
          ],
        ),
      );
    }
    return FilledButton(onPressed: onPressed, style: style, child: Text(label));
  }
}

/// Secondary/neutral action button.
class SecondaryActionButton extends StatelessWidget {
  const SecondaryActionButton({
    super.key,
    required this.onPressed,
    required this.label,
    this.icon,
    this.compact = false,
  });

  final VoidCallback? onPressed;
  final String label;
  final PwonyIconKind? icon;
  final bool compact;

  @override
  Widget build(BuildContext context) {
    final style = OutlinedButton.styleFrom(
      foregroundColor: AppColors.white,
      minimumSize: Size(compact ? 0 : 64, compact ? 40 : 44),
      padding: EdgeInsets.symmetric(
        horizontal: compact ? 14 : 18,
        vertical: compact ? 10 : 12,
      ),
      side: BorderSide(color: AppColors.border.withValues(alpha: 0.7)),
      textStyle: TextStyle(
        fontSize: compact ? 13 : 14,
        fontWeight: FontWeight.w500,
      ),
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(compact ? 12 : AppRadius.md),
      ),
    );
    if (icon != null) {
      return OutlinedButton(
        onPressed: onPressed,
        style: style,
        child: Row(
          mainAxisSize: MainAxisSize.min,
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            PwonyIcon(icon!, size: compact ? 16 : 18, color: AppColors.white),
            const SizedBox(width: 8),
            Text(label),
          ],
        ),
      );
    }
    return OutlinedButton(
        onPressed: onPressed, style: style, child: Text(label));
  }
}
