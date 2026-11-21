import 'dart:ui' show Color;

import '../model/board_geometry.dart';
import 'template_definition.dart';

/// Human heart template (biology).
///
/// Draws a heart shape with major vessels. Progressive drawing shows the
/// heart chamber outline first, then the major vessels, then internal details.
class HeartTemplate extends Template {
  HeartTemplate()
      : super(
          id: 'human_heart',
          category: TemplateCategory.biology,
          displayName: 'Human Heart',
          defaultSize: const BoardSize(0.28, 0.24),
          steps: const [
            // Step 1: Outer heart silhouette
            TemplateStep(
              id: 'heart_silhouette',
              action: TemplateDrawAction.drawPath,
              geometry: {
                'points': [
                  {'x': 0.15, 'y': 0.50},
                  {'x': 0.15, 'y': 0.10},
                  {'x': 0.30, 'y': 0.10},
                  {'x': 0.45, 'y': 0.20},
                  {'x': 0.45, 'y': 0.40},
                  {'x': 0.30, 'y': 0.50},
                  {'x': 0.15, 'y': 0.50},
                ],
                'closed': true,
                'smooth': true,
              },
              durationMs: 600,
              strokeWidth: 2.5,
            ),
            // Step 2: Aorta (top vessel)
            TemplateStep(
              id: 'aorta',
              action: TemplateDrawAction.drawPath,
              geometry: {
                'points': [
                  {'x': 0.40, 'y': 0.30},
                  {'x': 0.40, 'y': 0.08},
                  {'x': 0.55, 'y': 0.08},
                  {'x': 0.55, 'y': 0.20},
                ],
                'closed': false,
                'smooth': false,
              },
              durationMs: 400,
              delayMs: 100,
              strokeWidth: 2.0,
            ),
            // Step 3: Vena cava (right vessel)
            TemplateStep(
              id: 'vena_cava',
              action: TemplateDrawAction.drawPath,
              geometry: {
                'points': [
                  {'x': 0.25, 'y': 0.55},
                  {'x': 0.25, 'y': 0.12},
                  {'x': 0.15, 'y': 0.12},
                  {'x': 0.15, 'y': 0.30},
                ],
                'closed': false,
                'smooth': false,
              },
              durationMs: 400,
              delayMs: 150,
              strokeWidth: 1.8,
            ),
            // Step 4: Heart chambers detail (internal lines)
            TemplateStep(
              id: 'chambers',
              action: TemplateDrawAction.drawPath,
              geometry: {
                'points': [
                  {'x': 0.18, 'y': 0.35},
                  {'x': 0.32, 'y': 0.35},
                  {'x': 0.32, 'y': 0.45},
                  {'x': 0.18, 'y': 0.45},
                ],
                'closed': true,
                'smooth': false,
              },
              durationMs: 500,
              delayMs: 150,
              strokeWidth: 1.5,
            ),
            // Step 5: Label area
            TemplateStep(
              id: 'labels',
              action: TemplateDrawAction.fadeInLabel,
              durationMs: 200,
              delayMs: 100,
              strokeWidth: 0.0,
            ),
          ],
          labels: {
            'aorta_label': TemplateLabel(
              id: 'aorta_label',
              text: 'Aorta',
              position: const BoardPoint(0.52, 0.15),
              targetId: 'aorta',
              style: 'label',
            ),
            'vena_cava_label': TemplateLabel(
              id: 'vena_cava_label',
              text: 'Vena cava',
              position: const BoardPoint(0.12, 0.60),
              targetId: 'vena_cava',
              style: 'label',
            ),
            'chamber_label': TemplateLabel(
              id: 'chamber_label',
              text: 'Chambers',
              position: const BoardPoint(0.25, 0.42),
              targetId: 'chambers',
              style: 'caption',
            ),
          },
          animationMetadata: const {
            'easing': 'easeOutCubic',
            'overallDurationMs': 2600,
          },
        );
}
