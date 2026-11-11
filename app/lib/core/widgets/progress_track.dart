import 'package:flutter/material.dart';

import '../formatters.dart';
import '../theme.dart';

/// Thin rounded progress line with optional percentage label.
class ProgressTrack extends StatelessWidget {
  const ProgressTrack({
    super.key,
    required this.value,
    this.showLabel = false,
    this.color,
    this.height = 6,
  });

  /// 0..1 (values above 1 are treated as percentages).
  final double value;
  final bool showLabel;
  final Color? color;
  final double height;

  @override
  Widget build(BuildContext context) {
    final normalized = normalizeProgress(value);
    final track = ClipRRect(
      borderRadius: BorderRadius.circular(999),
      child: LinearProgressIndicator(
        value: normalized,
        minHeight: height,
        backgroundColor: AppColors.surfaceHigh,
        valueColor: AlwaysStoppedAnimation<Color>(color ?? AppColors.yellow),
      ),
    );
    if (!showLabel) return track;
    return Row(
      children: <Widget>[
        Expanded(child: track),
        const SizedBox(width: AppSpacing.md),
        SizedBox(
          width: 40,
          child: Text(
            percentLabel(normalized),
            textAlign: TextAlign.right,
            style: Theme.of(context).textTheme.labelMedium?.copyWith(
                  color: AppColors.chalk,
                ),
          ),
        ),
      ],
    );
  }
}
