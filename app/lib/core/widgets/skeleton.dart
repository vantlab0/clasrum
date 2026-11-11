import 'package:flutter/material.dart';

import '../theme.dart';

/// Opacity-pulse placeholder used while real data loads. No gradients, no
/// shimmer: a quiet breathing block that matches the chalk classroom.
class SkeletonPulse extends StatefulWidget {
  const SkeletonPulse({super.key, required this.child});

  final Widget child;

  @override
  State<SkeletonPulse> createState() => _SkeletonPulseState();
}

class _SkeletonPulseState extends State<SkeletonPulse>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 1100),
  )..repeat(reverse: true);

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return FadeTransition(
      opacity: Tween<double>(begin: 0.45, end: 0.9).animate(
        CurvedAnimation(parent: _controller, curve: Curves.easeInOut),
      ),
      child: widget.child,
    );
  }
}

class SkeletonBox extends StatelessWidget {
  const SkeletonBox({
    super.key,
    required this.height,
    this.width,
    this.radius = AppRadius.sm,
  });

  final double height;
  final double? width;
  final double radius;

  @override
  Widget build(BuildContext context) {
    return Container(
      height: height,
      width: width,
      decoration: BoxDecoration(
        color: AppColors.surfaceHigh,
        borderRadius: BorderRadius.circular(radius),
      ),
    );
  }
}

/// Placeholder mirroring the shape of a session card.
class SessionCardSkeleton extends StatelessWidget {
  const SessionCardSkeleton({super.key});

  @override
  Widget build(BuildContext context) {
    return SkeletonPulse(
      child: Container(
        padding: const EdgeInsets.all(AppSpacing.lg),
        decoration: BoxDecoration(
          color: AppColors.surface,
          borderRadius: BorderRadius.circular(AppRadius.lg),
          border: Border.all(color: AppColors.outlineSoft),
        ),
        child: const Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Row(
              children: <Widget>[
                Expanded(child: SkeletonBox(height: 16)),
                SizedBox(width: AppSpacing.lg),
                SkeletonBox(height: 20, width: 64, radius: 999),
              ],
            ),
            SizedBox(height: AppSpacing.md),
            SkeletonBox(height: 12, width: 180),
            SizedBox(height: AppSpacing.lg),
            SkeletonBox(height: 6, radius: 999),
            SizedBox(height: AppSpacing.md),
            SkeletonBox(height: 12, width: 120),
          ],
        ),
      ),
    );
  }
}

/// A column of [SessionCardSkeleton]s used by Home and Notes.
class ListSkeleton extends StatelessWidget {
  const ListSkeleton({super.key, this.itemCount = 3});

  final int itemCount;

  @override
  Widget build(BuildContext context) {
    return Column(
      children: <Widget>[
        for (var i = 0; i < itemCount; i++) ...<Widget>[
          if (i > 0) const SizedBox(height: AppSpacing.md),
          const SessionCardSkeleton(),
        ],
      ],
    );
  }
}
