import 'dart:ui' show Color;

import '../model/board_geometry.dart';
import 'template_definition.dart';

/// Reaction diagram template (chemistry).
///
/// Draws a chemical reaction A + B → C with arrows and energy indicator.
/// Progressive drawing shows reactants, then agents, then products.
class ReactionDiagramTemplate extends Template {
  ReactionDiagramTemplate()
      : super(
          id: 'reaction_diagram',
          category: TemplateCategory.chemistry,
          displayName: 'Reaction Diagram',
          defaultSize: const BoardSize(0.30, 0.16),
          steps: const [
            // Step 1: Reactant A
            TemplateStep(
              id: 'reactant_a',
              action: TemplateDrawAction.drawShape,
              geometry: {
                'shape': 'rect',
                'position': {'x': 0.10, 'y': 0.25},
                'size': {'w': 0.10, 'h': 0.10},
                'fillColor': 'primary',
              },
              durationMs: 400,
              strokeWidth: 2.0,
            ),
            // Step 2: Plus sign
            TemplateStep(
              id: 'plus_sign',
              action: TemplateDrawAction.drawText,
              geometry: {
                'text': ' + ',
                'position': {'x': 0.20, 'y': 0.30},
                'style': 'label',
              },
              durationMs: 200,
              delayMs: 50,
              strokeWidth: 0.0,
            ),
            // Step 3: Reactant B
            TemplateStep(
              id: 'reactant_b',
              action: TemplateDrawAction.drawShape,
              geometry: {
                'shape': 'rect',
                'position': {'x': 0.22, 'y': 0.25},
                'size': {'w': 0.10, 'h': 0.10},
                'fillColor': 'primary',
              },
              durationMs: 400,
              delayMs: 100,
              strokeWidth: 2.0,
            ),
            // Step 4: Reaction arrow (rightwards)
            TemplateStep(
              id: 'reaction_arrow',
              action: TemplateDrawAction.drawArrow,
              geometry: {
                'start': {'x': 0.32, 'y': 0.30},
                'end': {'x': 0.45, 'y': 0.30},
                'label': '→',
              },
              durationMs: 300,
              delayMs: 100,
              strokeWidth: 2.0,
            ),
            // Step 5: Product C
            TemplateStep(
              id: 'product_c',
              action: TemplateDrawAction.drawShape,
              geometry: {
                'shape': 'rect',
                'position': {'x': 0.50, 'y': 0.25},
                'size': {'w': 0.10, 'h': 0.10},
                'fillColor': 'secondary',
              },
              durationMs: 400,
              delayMs: 100,
              strokeWidth: 2.0,
            ),
            // Step 5b: Energy released (small downward arrow)
            TemplateStep(
              id: 'energy_released',
              action: TemplateDrawAction.drawArrow,
              geometry: {
                'start': {'x': 0.55, 'y': 0.30},
                'end': {'x': 0.65, 'y': 0.30},
                'label': 'ΔH < 0',
              },
              durationMs: 300,
              delayMs: 100,
              strokeWidth: 1.5,
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
            'a_label': TemplateLabel(
              id: 'a_label',
              text: 'A',
              position: const BoardPoint(0.15, 0.35),
              style: 'label',
            ),
            'b_label': TemplateLabel(
              id: 'b_label',
              text: 'B',
              position: const BoardPoint(0.27, 0.35),
              style: 'caption',
            ),
            'c_label': TemplateLabel(
              id: 'c_label',
              text: 'C',
              position: const BoardPoint(0.55, 0.35),
              style: 'caption',
            ),
            'energy_label': TemplateLabel(
              id: 'energy_label',
              text: 'Exothermic',
              position: const BoardPoint(0.68, 0.30),
              style: 'caption',
            ),
          },
          animationMetadata: const {
            'easing': 'easeOutCubic',
            'overallDurationMs': 2300,
          },
        );
}
