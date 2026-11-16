import 'dart:ui' show Color;

import '../model/board_geometry.dart';

// ———————————————————————————————————————————————————————————————————
// Minimal Template class kept for backward compatibility.
// The real system uses the generative drawing protocol (DrawingStep,
// DrawingAction, DrawingPayload) defined in board_event.dart.
// ———————————————————————————————————————————————————————————————————

/// A minimal template class kept for backward compatibility.
/// Do not use for new development — use the generative drawing protocol
/// instead.
class Template {
  const Template({
    required this.id,
    required this.category,
    required this.displayName,
    required this.steps,
    this.labels = const {},
    required this.defaultSize,
    this.animationMetadata,
  });

  /// Unique template identifier.
  final String id;

  /// Template category.
  final TemplateCategory category;

  /// Human-readable display name.
  final String displayName;

  /// Ordered drawing steps (backward-compat only; use DrawingStep instead).
  final List<DrawingStep> steps;

  /// Template labels keyed by label ID.
  final Map<String, TemplateLabel> labels;

  /// Default normalized dimensions (width, height in 0..1).
  final BoardSize defaultSize;

  /// Optional animation metadata.
  final Map<String, dynamic>? animationMetadata;

  /// Returns the label with [id], or null if not found.
  TemplateLabel? label(String id) => labels[id];

  /// Returns step IDs in execution order.
  List<String> get stepIds => steps.map((s) => s.id).toList();

  /// Total estimated animation duration (sum of step durations + delays).
  double get totalDurationMs {
    var total = 0.0;
    for (final step in steps) {
      total += step.delayMs + step.durationMs;
    }
    return total;
  }
}

/// A text label attached to a drawing element.
class TemplateLabel {
  const TemplateLabel({
    required this.id,
    required this.text,
    required this.position,
    this.targetId,
    this.style = 'label',
    this.color,
  });

  /// Unique label identifier within the drawing.
  final String id;

  /// Label text.
  final String text;

  /// Normalized position relative to the drawing origin.
  final BoardPoint position;

  /// Element ID this label points to (for leader lines).
  final String? targetId;

  /// Text style name ('label', 'caption', 'note', etc.).
  final String style;

  /// Label color (falls back to chalk primary).
  final Color? color;
}

/// Category of an educational drawing.
enum TemplateCategory {
  biology,
  physics,
  mathematics,
  chemistry,
}