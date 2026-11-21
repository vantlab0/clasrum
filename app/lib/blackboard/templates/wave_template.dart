import 'dart:ui' show Color;

import '../model/board_geometry.dart';
import 'template_definition.dart';

/// Wave template (physics).
///
/// Draws a transverse wave with crest and trough labels. Progressive drawing
/// reveals from the rest position to the full amplitude.
class WaveTemplate extends Template {
  WaveTemplate()
      : super(
          id: 'wave',
          category: TemplateCategory.physics,
          displayName: 'Wave',
          defaultSize: const BoardSize(0.30, 0.16),
          steps: const [
            // Step 1: Rest position (horizontal line)
            TemplateStep(
              id: 'rest_position',
              action: TemplateDrawAction.drawLine,
              geometry: {
                'start': {'x': 0.10, 'y': 0.50},
                'end': {'x': 0.90, 'y': 0.50},
              },
              durationMs: 400,
              strokeWidth: 1.5,
            ),
            // Step 2: Crest (first peak)
            TemplateStep(
              id: 'crest_1',
              action: TemplateDrawAction.drawPath,
              geometry: {
                'points': [
                  {'x': 0.25, 'y': 0.40},
                  {'x': 0.35, 'y': 0.30},
                  {'x': 0.45, 'y': 0.40},
                  {'x': 0.55, 'y': 0.30},
                  {'x': 0.65, 'y': 0.40},
                ],
                'closed': false,
                'smooth': true,
              },
              durationMs: 500,
              delayMs: 100,
              strokeWidth: 2.0,
            ),
            // Step 3: Second crest
            TemplateStep(
              id: 'crest_2',
              action: TemplateDrawAction.drawPath,
              geometry: {
                'points': [
                  {'x': 0.75, 'y': 0.30},
                  {'x': 0.85, 'y': 0.20},
                  {'x': 0.90, 'y': 0.35},
                ],
                'closed': false,
                'smooth': true,
              },
              durationMs: 500,
              delayMs: 100,
              strokeWidth: 2.0,
            ),
            // Step 4: Trough (first valley)
            TemplateStep(
              id: 'trough_1',
              action: TemplateDrawAction.drawPath,
              geometry: {
                'points': [
                  {'x': 0.25, 'y': 0.60},
                  {'x': 0.35, 'y': 0.70},
                  {'x': 0.45, 'y': 0.60},
                  {'x': 0.55, 'y': 0.70},
                  {'x': 0.65, 'y': 0.60},
                ],
                'closed': false,
                'smooth': true,
              },
              durationMs: 500,
              delayMs: 100,
              strokeWidth: 2.0,
            ),
            // Step 5: Labels
            TemplateStep(
              id: 'labels',
              action: TemplateDrawAction.fadeInLabel,
              durationMs: 200,
              delayMs: 100,
              strokeWidth: 0.0,
            ),
          ],
          labels: {
            'rest_label': TemplateLabel(
              id: 'rest_label',
              text: 'Rest position',
              position: const BoardPoint(0.45, 0.55),
              style: 'label',
            ),
            'crest_label': TemplateLabel(
              id: 'crest_label',
              text: 'Crest',
              position: const BoardPoint(0.52, 0.32),
              style: 'caption',
            ),
            'trough_label': TemplateLabel(
              id: 'trough_label',
              text: 'Trough',
              position: const BoardPoint(0.52, 0.68),
              style: 'caption',
            ),
            'amplitude_label': TemplateLabel(
              id: 'amplitude_label',
              text: 'Amplitude',
              position: const BoardPoint(0.15, 0.42),
              style: 'caption',
            ),
            'wavelength_label': TemplateLabel(
              id: 'wavelength_label',
              text: 'Wavelength',
              position: const BoardPoint(0.75, 0.52),
              style: 'caption',
            ),
          },
          animationMetadata: const {
            'easing': 'easeOutCubic',
            'overallDurationMs': 2800,
          },
        );
}
