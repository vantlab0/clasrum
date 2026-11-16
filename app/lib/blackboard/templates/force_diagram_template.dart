import 'dart:ui' show Color;

import '../../model/board_geometry.dart';
import '../template_definition.dart';

/// Force diagram template (physics).
///
/// Draws a box with applied force vectors. Progressive drawing shows the
/// object first, then the force vectors acting on it.
class ForceDiagramTemplate extends Template {
  ForceDiagramTemplate() : super(
    id: 'force_diagram',
    category: TemplateCategory.physics,
    displayName: 'Force Diagram',
    defaultSize: const BoardSize(0.24, 0.18),
    steps: const [
      // Step 1: Object box (rectangle)
      TemplateStep(
        id: 'object_box',
        action: TemplateDrawAction.drawShape,
        geometry: {
          'shape': 'rect',
          'position': {'x': 0.20, 'y': 0.25},
          'size': {'w': 0.12, 'h': 0.10},
        },
        durationMs: 400,
        strokeWidth: 2.5,
      ),
      // Step 2: Force F1 (arrow pointing up)
      TemplateStep(
        id: 'force_f1',
        action: TemplateDrawAction.drawArrow,
        geometry: {
          'start': {'x': 0.26, 'y': 0.20},
          'end': {'x': 0.26, 'y': 0.12},
          'label': 'F₁',
        },
        durationMs: 400,
        delayMs: 100,
        strokeWidth: 2.0,
      ),
      // Step 3: Force F2 (arrow pointing right)
      TemplateStep(
        id: 'force_f2',
        action: TemplateDrawAction.drawArrow,
        geometry: {
          'start': {'x': 0.30, 'y': 0.26},
          'end': {'x': 0.42, 'y': 0.26},
          'label': 'F₂',
        },
        durationMs: 400,
        delayMs: 100,
        strokeWidth: 2.0,
      ),
      // Step 4: Force F3 (arrow pointing down)
      TemplateStep(
        id: 'force_f3',
        action: TemplateDrawAction.drawArrow,
        geometry: {
          'start': {'x': 0.26, 'y': 0.35},
          'end': {'x': 0.26, 'y': 0.43},
          'label': 'F₃',
        },
        durationMs: 400,
        delayMs: 100,
        strokeWidth: 2.0,
      ),
      // Step 5: Net force label
      TemplateStep(
        id: 'net_force',
        action: TemplateDrawAction.drawText,
        geometry: {
          'text': 'ΣF = 0',
          'position': {'x': 0.20, 'y': 0.10},
          'style': 'label',
        },
        durationMs: 300,
        delayMs: 100,
        strokeWidth: 0.0,
      ),
      // Step 6: Labels
      TemplateStep(
        id: 'labels',
        action: TemplateDrawAction.fadeInLabel,
        durationMs: 200,
        delayMs: 100,
        strokeWidth: 0.0,
      ),
    ],
    labels: {
      'object_label': TemplateLabel(
        id: 'object_label',
        text: 'Object',
        position: const BoardPoint(0.18, 0.40),
        style: 'label',
      ),
      'f1_label': TemplateLabel(
        id: 'f1_label',
        text: 'F₁ ↑',
        position: const BoardPoint(0.22, 0.15),
        style: 'caption',
      ),
      'f2_label': TemplateLabel(
        id: 'f2_label',
        text: 'F₂ →',
        position: const BoardPoint(0.38, 0.28),
        style: 'caption',
      ),
      'f3_label': TemplateLabel(
        id: 'f3_label',
        text: 'F₃ ↓',
        position: const BoardPoint(0.22, 0.45),
        style: 'caption',
      ),
      'net_label': TemplateLabel(
        id: 'net_label',
        text: 'Equilibrium',
        position: const BoardPoint(0.20, 0.08),
        style: 'caption',
      ),
    },
    animationMetadata: const {
      'easing': 'easeOutCubic',
      'overallDurationMs': 2400,
    },
  );
}