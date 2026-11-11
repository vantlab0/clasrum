import 'package:flutter/material.dart';

import '../theme.dart';

/// A soft, chalkboard-style card: charcoal fill, hairline border, generous
/// radius. Used everywhere content groups together.
class ChalkCard extends StatelessWidget {
  const ChalkCard({
    super.key,
    required this.child,
    this.padding = const EdgeInsets.all(AppSpacing.lg),
    this.onTap,
    this.accent,
    this.borderColor,
    this.backgroundColor,
    this.semanticLabel,
  });

  final Widget child;
  final EdgeInsetsGeometry padding;
  final VoidCallback? onTap;

  /// Optional 3px accent bar on the left edge (e.g. lesson status colour).
  final Color? accent;
  final Color? borderColor;
  final Color? backgroundColor;
  final String? semanticLabel;

  @override
  Widget build(BuildContext context) {
    final radius = BorderRadius.circular(AppRadius.lg);
    final content = Padding(padding: padding, child: child);

    return Semantics(
      label: semanticLabel,
      button: onTap != null,
      container: true,
      child: Material(
        color: backgroundColor ?? AppColors.surface,
        shape: RoundedRectangleBorder(
          borderRadius: radius,
          side: BorderSide(color: borderColor ?? AppColors.outlineSoft),
        ),
        clipBehavior: Clip.antiAlias,
        child: InkWell(
          onTap: onTap,
          customBorder: RoundedRectangleBorder(borderRadius: radius),
          child: accent == null
              ? content
              : Stack(
                  children: <Widget>[
                    Positioned(
                      left: 0,
                      top: 0,
                      bottom: 0,
                      child: Container(width: 3, color: accent),
                    ),
                    Padding(
                      padding: const EdgeInsets.only(left: 3),
                      child: content,
                    ),
                  ],
                ),
        ),
      ),
    );
  }
}
