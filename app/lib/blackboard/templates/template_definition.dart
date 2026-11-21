import 'dart:ui' show Color;

import '../model/board_geometry.dart';

/// Drawing primitives supported by the legacy template system.
enum TemplateDrawAction {
  drawShape,
  drawLine,
  drawPath,
  drawArrow,
  drawText,
  highlight,
  fadeInLabel,
}

/// One legacy drawing step kept for compatibility with the educational
/// templates.
class TemplateStep {
  const TemplateStep({
    required this.id,
    required this.action,
    this.geometry = const <String, Object?>{},
    required this.durationMs,
    this.delayMs = 0,
    this.strokeWidth = 2.0,
    this.labelId,
    this.targetId,
    this.color,
  });

  final String id;
  final TemplateDrawAction action;
  final Map<String, Object?> geometry;
  final int durationMs;
  final int delayMs;
  final double strokeWidth;
  final String? labelId;
  final String? targetId;
  final dynamic color;
}

/// Unique category labels kept for template compatibility.

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
  final List<TemplateStep> steps;

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
