/// Formatting helpers shared by the classroom UI.
library;

const List<String> learningLevels = <String>['beginner', 'intermediate', 'advanced'];

const List<int> supportedDurations = <int>[15, 30, 45, 60];

String levelLabel(String level) {
  switch (level) {
    case 'beginner':
      return 'Beginner';
    case 'intermediate':
      return 'Intermediate';
    case 'advanced':
      return 'Advanced';
    default:
      if (level.isEmpty) return 'Beginner';
      return '${level[0].toUpperCase()}${level.substring(1)}';
  }
}

String durationLabel(int minutes) => '$minutes min';

String lessonStatusLabel(String status) {
  switch (status) {
    case 'created':
      return 'Ready';
    case 'live':
      return 'Live';
    case 'paused':
      return 'Paused';
    case 'completed':
      return 'Completed';
    case 'error':
      return 'Needs attention';
    default:
      if (status.isEmpty) return 'Draft';
      return '${status[0].toUpperCase()}${status.substring(1)}';
  }
}

String noteStatusLabel(String status) {
  switch (status) {
    case 'pending':
      return 'Preparing';
    case 'ready':
      return 'Ready';
    case 'failed':
      return 'Failed';
    default:
      if (status.isEmpty) return 'Draft';
      return '${status[0].toUpperCase()}${status.substring(1)}';
  }
}

/// `just now`, `4m ago`, `3h ago`, `Yesterday`, `12 Nov`.
String relativeTime(DateTime? time, {DateTime? now}) {
  if (time == null) return 'Not started yet';
  final reference = now ?? DateTime.now();
  final delta = reference.difference(time);
  if (delta.isNegative) return 'Just now';
  if (delta.inSeconds < 45) return 'Just now';
  if (delta.inMinutes < 60) return '${delta.inMinutes}m ago';
  if (delta.inHours < 24) return '${delta.inHours}h ago';
  if (delta.inDays == 1) return 'Yesterday';
  if (delta.inDays < 7) return '${delta.inDays}d ago';
  return '${time.day} ${_monthShort(time.month)}';
}

/// `mm:ss` for short lessons, `h:mm:ss` past an hour.
String clockDuration(int seconds) {
  final safe = seconds < 0 ? 0 : seconds;
  final hours = safe ~/ 3600;
  final minutes = (safe % 3600) ~/ 60;
  final secs = safe % 60;
  final two = (int value) => value.toString().padLeft(2, '0');
  if (hours > 0) {
    return '$hours:${two(minutes)}:${two(secs)}';
  }
  return '${two(minutes)}:${two(secs)}';
}

/// `42%` (input is a 0..1 fraction, tolerating 0..100 from legacy payloads).
String percentLabel(double fraction) {
  final normalized = fraction > 1 ? fraction / 100 : fraction;
  final clamped = normalized.clamp(0.0, 1.0);
  return '${(clamped * 100).round()}%';
}

/// Progress normalized to 0..1 regardless of whether the backend sent 0..1 or
/// 0..100. Contracts define progress as a float; we accept both shapes.
double normalizeProgress(double progress) {
  if (progress.isNaN) return 0;
  if (progress > 1) return (progress / 100).clamp(0.0, 1.0);
  return progress.clamp(0.0, 1.0);
}

String _monthShort(int month) {
  const months = <String>[
    'Jan',
    'Feb',
    'Mar',
    'Apr',
    'May',
    'Jun',
    'Jul',
    'Aug',
    'Sep',
    'Oct',
    'Nov',
    'Dec',
  ];
  if (month < 1 || month > 12) return '';
  return months[month - 1];
}

String dateLabel(DateTime? time) {
  if (time == null) return '—';
  return '${time.day} ${_monthShort(time.month)} ${time.year}';
}

/// `Tutor` / `You` / `Classroom`.
String speakerLabel(String speaker) {
  switch (speaker) {
    case 'tutor':
      return 'Tutor';
    case 'student':
      return 'You';
    case 'system':
      return 'Classroom';
    default:
      return 'Classroom';
  }
}
