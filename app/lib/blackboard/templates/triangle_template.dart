import 'dart:ui' show Color;

import '../model/board_geometry.dart';
import 'template_definition.dart';

/// Triangle template (mathematics).
///
/// Draws a triangle with labeled vertices and side labels.
/// Progressive drawing shows each side being drawn, then labels appearing.
class TriangleTemplate extends Template {
  TriangleTemplate()
      : super(
          id: 'triangle',
          category: TemplateCategory.mathematics,
          displayName: 'Triangle',
          defaultSize: const BoardSize(0.20, 0.18),
          steps: const [
            // Step 1: Side A-B
            TemplateStep(
              id: 'side_ab',
              action: TemplateDrawAction.drawLine,
              geometry: {
                'start': {'x': 0.10, 'y': 0.16},
                'end': {'x': 0.30, 'y': 0.16},
              },
              durationMs: 500,
              strokeWidth: 2.5,
            ),
            // Step 2: Side B-C
            TemplateStep(
              id: 'side_bc',
              action: TemplateDrawAction.drawLine,
              geometry: {
                'start': {'x': 0.30, 'y': 0.16},
                'end': {'x': 0.20, 'y': 0.32},
              },
              durationMs: 500,
              delayMs: 100,
              strokeWidth: 2.5,
            ),
            // Step 3: Side C-A
            TemplateStep(
              id: 'side_ca',
              action: TemplateDrawAction.drawLine,
              geometry: {
                'start': {'x': 0.20, 'y': 0.32},
                'end': {'x': 0.10, 'y': 0.16},
              },
              durationMs: 500,
              delayMs: 200,
              labelId: 'triangle_label',
              strokeWidth: 2.5,
            ),
          ],
          labels: {
            'triangle_label': TemplateLabel(
              id: 'triangle_label',
              text: 'Triangle ABC',
              position: const BoardPoint(0.32, 0.24),
              targetId: 'side_bc',
              style: 'label',
            ),
          },
          animationMetadata: const {
            'easing': 'easeOutCubic',
            'overallDurationMs': 1600,
          },
        );
}
