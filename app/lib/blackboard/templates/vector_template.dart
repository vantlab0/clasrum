import 'dart:ui' show Color;

import '../model/board_geometry.dart';
import 'template_definition.dart';

/// Vector template (mathematics).
///
/// Draws a vector with magnitude arrow and component breakdown. Progressive
/// drawing shows the vector arrow first, then the components.
class VectorTemplate extends Template {
  VectorTemplate()
      : super(
          id: 'vector',
          category: TemplateCategory.mathematics,
          displayName: 'Vector',
          defaultSize: const BoardSize(0.22, 0.16),
          steps: const [
            // Step 1: Vector origin point
            TemplateStep(
              id: 'origin',
              action: TemplateDrawAction.drawShape,
              geometry: {
                'shape': 'circle',
                'position': {'x': 0.12, 'y': 0.35},
                'size': {'w': 0.02, 'h': 0.02},
                'fillColor': 'primary',
              },
              durationMs: 300,
              strokeWidth: 2.0,
            ),
            // Step 2: Vector arrow (directed line)
            TemplateStep(
              id: 'vector_arrow',
              action: TemplateDrawAction.drawArrow,
              geometry: {
                'start': {'x': 0.12, 'y': 0.35},
                'end': {'x': 0.40, 'y': 0.35},
                'label': 'v',
              },
              durationMs: 400,
              delayMs: 100,
              strokeWidth: 2.0,
            ),
            // Step 3: Horizontal component
            TemplateStep(
              id: 'horizontal_comp',
              action: TemplateDrawAction.drawLine,
              geometry: {
                'start': {'x': 0.12, 'y': 0.35},
                'end': {'x': 0.40, 'y': 0.35},
              },
              durationMs: 400,
              delayMs: 100,
              strokeWidth: 1.5,
            ),
            // Step 4: Vertical component (zero for this example)
            TemplateStep(
              id: 'vertical_comp',
              action: TemplateDrawAction.drawLine,
              geometry: {
                'start': {'x': 0.12, 'y': 0.35},
                'end': {'x': 0.12, 'y': 0.35},
              },
              durationMs: 200,
              delayMs: 50,
              strokeWidth: 1.0,
            ),
            // Step 5: Component labels
            TemplateStep(
              id: 'component_labels',
              action: TemplateDrawAction.drawText,
              geometry: {
                'text': 'vₓ = 3, vᵧ = 0',
                'position': {'x': 0.45, 'y': 0.30},
                'style': 'label',
              },
              durationMs: 300,
              delayMs: 100,
              strokeWidth: 0.0,
            ),
            // Step 6: Magnitude label
            TemplateStep(
              id: 'magnitude',
              action: TemplateDrawAction.drawText,
              geometry: {
                'text': '|v| = 3',
                'position': {'x': 0.45, 'y': 0.40},
                'style': 'caption',
              },
              durationMs: 300,
              delayMs: 100,
              strokeWidth: 0.0,
            ),
            // Step 7: Labels
            TemplateStep(
              id: 'final_labels',
              action: TemplateDrawAction.fadeInLabel,
              durationMs: 200,
              delayMs: 100,
              strokeWidth: 0.0,
            ),
          ],
          labels: {
            'origin_label': TemplateLabel(
              id: 'origin_label',
              text: 'Initial point',
              position: const BoardPoint(0.10, 0.32),
              style: 'label',
            ),
            'v_label': TemplateLabel(
              id: 'v_label',
              text: 'v',
              position: const BoardPoint(0.42, 0.38),
              style: 'label',
            ),
            'vx_label': TemplateLabel(
              id: 'vx_label',
              text: 'vₓ',
              position: const BoardPoint(0.48, 0.32),
              style: 'caption',
            ),
            'vy_label': TemplateLabel(
              id: 'vy_label',
              text: 'vᵧ',
              position: const BoardPoint(0.48, 0.43),
              style: 'caption',
            ),
            'magnitude_label': TemplateLabel(
              id: 'magnitude_label',
              text: '|v|',
              position: const BoardPoint(0.48, 0.48),
              style: 'caption',
            ),
          },
          animationMetadata: const {
            'easing': 'easeOutCubic',
            'overallDurationMs': 2300,
          },
        );
}
