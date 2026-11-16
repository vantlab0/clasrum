import 'dart:ui' show Color;

import '../../model/board_geometry.dart';
import '../template_definition.dart';

/// DNA double helix template (biology).
///
/// Draws a DNA double helix with base pairs. Progressive drawing reveals
/// from the top down, showing the spiral structure and base pairing.
class DNAHelixTemplate extends Template {
  DNAHelixTemplate() : super(
    id: 'dna_helix',
    category: TemplateCategory.biology,
    displayName: 'DNA Double Helix',
    defaultSize: const BoardSize(0.28, 0.28),
    steps: const [
      // Step 1: Backbone rails (two vertical lines)
      TemplateStep(
        id: 'backbone_left',
        action: TemplateDrawAction.drawPath,
        geometry: {
          'points': [
            {'x': 0.15, 'y': 0.10},
            {'x': 0.15, 'y': 0.90},
          ],
          'closed': false,
          'smooth': false,
        },
        durationMs: 500,
        strokeWidth: 2.0,
      ),
      TemplateStep(
        id: 'backbone_right',
        action: TemplateDrawAction.drawPath,
        geometry: {
          'points': [
            {'x': 0.30, 'y': 0.10},
            {'x': 0.30, 'y': 0.90},
          ],
          'closed': false,
          'smooth': false,
        },
        durationMs: 500,
        delayMs: 200,
        strokeWidth: 2.0,
      ),
      // Step 2: Base pairs (rungs)
      TemplateStep(
        id: 'base_pair_a',
        action: TemplateDrawAction.drawLine,
        geometry: {
          'start': {'x': 0.18, 'y': 0.35},
          'end': {'x': 0.27, 'y': 0.35},
        },
        durationMs: 300,
        delayMs: 50,
        strokeWidth: 1.5,
      ),
      TemplateStep(
        id: 'base_pair_c',
        action: TemplateDrawAction.drawLine,
        geometry: {
          'start': {'x': 0.18, 'y': 0.50},
          'end': {'x': 0.27, 'y': 0.50},
        },
        durationMs: 300,
        delayMs: 50,
        strokeWidth: 1.5,
      ),
      TemplateStep(
        id: 'base_pair_g',
        action: TemplateDrawAction.drawLine,
        geometry: {
          'start': {'x': 0.18, 'y': 0.65},
          'end': {'x': 0.27, 'y': 0.65},
        },
        durationMs: 300,
        delayMs: 50,
        strokeWidth: 1.5,
      ),
      TemplateStep(
        id: 'base_pair_t',
        action: TemplateDrawAction.drawLine,
        geometry: {
          'start': {'x': 0.18, 'y': 0.80},
          'end': {'x': 0.27, 'y': 0.80},
        },
        durationMs: 300,
        delayMs: 50,
        strokeWidth: 1.5,
      ),
      // Add more base pairs progressively
      TemplateStep(
        id: 'base_pair_more_a',
        action: TemplateDrawAction.drawLine,
        geometry: {
          'start': {'x': 0.18, 'y': 0.45},
          'end': {'x': 0.27, 'y': 0.45},
        },
        durationMs: 300,
        delayMs: 50,
        strokeWidth: 1.5,
      ),
      TemplateStep(
        id: 'base_pair_more_c',
        action: TemplateDrawAction.drawLine,
        geometry: {
          'start': {'x': 0.18, 'y': 0.70},
          'end': {'x': 0.27, 'y': 0.70},
        },
        durationMs: 300,
        delayMs: 50,
        strokeWidth: 1.5,
      ),
      // Step 3: Highlight the structure
      TemplateStep(
        id: 'highlight',
        action: TemplateDrawAction.highlight,
        geometry: {'targetIds': ['backbone_left', 'backbone_right']},
        durationMs: 300,
        delayMs: 100,
        strokeWidth: 0.0,
      ),
    ],
    labels: {
      'backbone_label': TemplateLabel(
        id: 'backbone_label',
        text: 'DNA helix',
        position: const BoardPoint(0.38, 0.50),
        style: 'label',
      ),
      'base_label_a': TemplateLabel(
        id: 'base_label_a',
        text: 'A-T',
        position: const BoardPoint(0.28, 0.38),
        style: 'caption',
      ),
      'base_label_c': TemplateLabel(
        id: 'base_label_c',
        text: 'C-G',
        position: const BoardPoint(0.28, 0.53),
        style: 'caption',
      ),
      'base_label_g': TemplateLabel(
        id: 'base_label_g',
        text: 'G-C',
        position: const BoardPoint(0.28, 0.68),
        style: 'caption',
      ),
      'base_label_t': TemplateLabel(
        id: 'base_label_t',
        text: 'A-T',
        position: const BoardPoint(0.28, 0.83),
        style: 'caption',
      ),
    },
    animationMetadata: const {
      'easing': 'easeOutCubic',
      'overallDurationMs': 4200,
    },
  );
}