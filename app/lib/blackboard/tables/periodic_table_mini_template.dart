import 'dart:ui' show Color;

import '../model/board_geometry.dart';
import '../templates/template_definition.dart';

// Minimal periodic table element template - shows one element box
class PeriodicElementTemplate extends Template {
  PeriodicElementTemplate()
      : super(
          id: 'periodic_element',
          category: TemplateCategory.chemistry,
          displayName: 'Element',
          defaultSize: const BoardSize(0.12, 0.10),
          steps: const [
            // Step 1: Element box
            TemplateStep(
              id: 'element_box',
              action: TemplateDrawAction.drawShape,
              geometry: {
                'shape': 'rect',
                'position': {'x': 0.0, 'y': 0.0},
                'size': {'w': 0.12, 'h': 0.10},
              },
              durationMs: 300,
              strokeWidth: 1.5,
            ),
            // Step 2: Element symbol
            TemplateStep(
              id: 'symbol',
              action: TemplateDrawAction.drawText,
              geometry: {
                'text': 'H',
                'position': {'x': 0.02, 'y': 0.02},
                'style': 'label',
              },
              durationMs: 200,
              delayMs: 50,
              strokeWidth: 0.0,
            ),
            // Step 3: Atomic number
            TemplateStep(
              id: 'atomic_number',
              action: TemplateDrawAction.drawText,
              geometry: {
                'text': '1',
                'position': {'x': 0.02, 'y': 0.06},
                'style': 'caption',
              },
              durationMs: 200,
              delayMs: 50,
              strokeWidth: 0.0,
            ),
            // Step 4: Labels
            TemplateStep(
              id: 'labels',
              action: TemplateDrawAction.fadeInLabel,
              durationMs: 200,
              delayMs: 100,
              strokeWidth: 0.0,
            ),
          ],
          labels: {
            'symbol_label': TemplateLabel(
              id: 'symbol_label',
              text: 'Symbol',
              position: const BoardPoint(0.15, 0.05),
              style: 'label',
            ),
            'number_label': TemplateLabel(
              id: 'number_label',
              text: 'Atomic #',
              position: const BoardPoint(0.15, 0.09),
              style: 'caption',
            ),
          },
          animationMetadata: const {
            'easing': 'easeOutCubic',
            'overallDurationMs': 1100,
          },
        );
}
