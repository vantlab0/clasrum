import 'package:flutter/material.dart';

import '../theme.dart';

/// Compact status pill. Colours come from the fixed chalk palette.
class StatusChip extends StatelessWidget {
  const StatusChip({
    super.key,
    required this.label,
    required this.color,
    this.icon,
    this.dense = false,
  });

  final String label;
  final Color color;
  final IconData? icon;
  final bool dense;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: EdgeInsets.symmetric(
        horizontal: dense ? AppSpacing.sm : AppSpacing.md,
        vertical: dense ? 2 : AppSpacing.xs,
      ),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.14),
        borderRadius: BorderRadius.circular(999),
        border: Border.all(color: color.withValues(alpha: 0.4)),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          if (icon != null) ...<Widget>[
            Icon(icon, size: 12, color: color),
            const SizedBox(width: AppSpacing.xs),
          ],
          Text(
            label,
            style: TextStyle(
              fontSize: 11,
              fontWeight: FontWeight.w600,
              letterSpacing: 0.3,
              color: color,
            ),
          ),
        ],
      ),
    );
  }
}

/// Status pill for a lesson lifecycle state.
class LessonStatusChip extends StatelessWidget {
  const LessonStatusChip({super.key, required this.status, this.dense = false});

  final String status;
  final bool dense;

  @override
  Widget build(BuildContext context) {
    final (String label, Color color, IconData icon) = switch (status) {
      'live' => ('Live', AppColors.green, Icons.radio_button_checked_rounded),
      'paused' => ('Paused', AppColors.yellow, Icons.pause_circle_outline_rounded),
      'completed' => ('Completed', AppColors.blue, Icons.check_circle_outline_rounded),
      'error' => ('Needs attention', AppColors.red, Icons.error_outline_rounded),
      'created' => ('Ready', AppColors.chalkMuted, Icons.bolt_rounded),
      _ => (
          status.isEmpty ? 'Draft' : status,
          AppColors.chalkMuted,
          Icons.circle_outlined,
        ),
    };
    return StatusChip(label: label, color: color, icon: icon, dense: dense);
  }
}

/// Status pill for a generated-notes record.
class NoteStatusChip extends StatelessWidget {
  const NoteStatusChip({super.key, required this.status, this.dense = false});

  final String status;
  final bool dense;

  @override
  Widget build(BuildContext context) {
    final (String label, Color color, IconData icon) = switch (status) {
      'ready' => ('Ready', AppColors.green, Icons.check_rounded),
      'pending' => ('Preparing', AppColors.yellow, Icons.hourglass_top_rounded),
      'failed' => ('Failed', AppColors.red, Icons.error_outline_rounded),
      _ => ('Draft', AppColors.chalkMuted, Icons.notes_rounded),
    };
    return StatusChip(label: label, color: color, icon: icon, dense: dense);
  }
}
