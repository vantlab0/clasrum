import 'dart:ui';

import 'package:flutter/material.dart';

import '../theme.dart';
import 'clasrum_icons.dart';

/// Translucent glass surface with controlled blur and subtle border.
class GlassContainer extends StatelessWidget {
  const GlassContainer({
    super.key,
    required this.child,
    this.padding,
    this.margin,
    this.radius = AppRadius.lg,
    this.borderOpacity = 0.5,
    this.blurSigma = 10,
    this.backgroundOpacity = 0.45,
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
    Widget content = Container(
      padding: padding,
      decoration: BoxDecoration(
        color: base.withValues(alpha: backgroundOpacity),
        borderRadius: BorderRadius.circular(radius),
        border: Border.all(color: border.withValues(alpha: borderOpacity)),
      ),
      child: child,
    );
    if (blurSigma > 0) {
      content = ClipRRect(
        borderRadius: BorderRadius.circular(radius),
        child: BackdropFilter(
          filter: ImageFilter.blur(sigmaX: blurSigma, sigmaY: blurSigma),
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
    this.backgroundOpacity = 0.45,
    this.borderOpacity = 0.5,
    this.blurSigma = 8,
    this.iconColor = AppColors.white,
  });

  final ClasrumIconKind icon;
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
              child: ClasrumIcon(icon, size: iconSize, color: iconColor),
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
        blurSigma: 14,
        backgroundOpacity: 0.72,
        borderOpacity: 0.55,
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

/// Primary action button — use selectively (blue).
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
  final ClasrumIconKind? icon;
  final bool compact;

  @override
  Widget build(BuildContext context) {
    final style = FilledButton.styleFrom(
      backgroundColor: AppColors.blue,
      foregroundColor: AppColors.white,
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
            ClasrumIcon(icon!, size: compact ? 16 : 18, color: AppColors.white),
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
  final ClasrumIconKind? icon;
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
            ClasrumIcon(icon!, size: compact ? 16 : 18, color: AppColors.white),
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
