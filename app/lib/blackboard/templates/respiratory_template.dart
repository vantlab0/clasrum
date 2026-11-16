import 'dart:ui' show Color;

import '../../model/board_geometry.dart';
import '../template_definition.dart';

/// Respiratory system template (biology).
///
/// Draws lungs with trachea and bronchial tree. Progressive drawing reveals
/// from the trachea down to the alveoli clusters.
class RespiratoryTemplate extends Template {
  RespiratoryTemplate() : super(
    id: 'respiratory_system',
    category: TemplateCategory.biology,
    displayName: 'Respiratory System',
    defaultSize: const BoardSize(0.32, 0.26),
    steps: const [
      // Step 1: Trachea (windpipe)
      TemplateStep(
        id: 'trachea',
        action: TemplateDrawAction.drawPath,
        geometry: {
          'points': [
            {'x': 0.20, 'y': 0.60},
            {'x': 0.20, 'y': 0.30},
            {'x': 0.35, 'y': 0.30},
            {'x': 0.35, 'y': 0.45},
            {'x': 0.20, 'y': 0.45},
            {'x': 0.20, 'y': 0.60},
          ],
          'closed': true,
          'smooth': true,
        },
        durationMs: 500,
        strokeWidth: 2.5,
      ),
      // Step 2: Primary bronchi (two branches)
      TemplateStep(
        id: 'primary_bronchi',
        action: TemplateDrawAction.drawPath,
        geometry: {
          'points': [
            {'x': 0.30, 'y': 0.40},
            {'x': 0.30, 'y': 0.25},
            {'x': 0.45, 'y': 0.25},
            {'x': 0.45, 'y': 0.35},
          ],
          'closed': false,
          'smooth': true,
        },
        durationMs: 400,
        delayMs: 100,
        strokeWidth: 2.0,
      ),
      TemplateStep(
        id: 'primary_bronchi_left',
        action: TemplateDrawAction.drawPath,
        geometry: {
          'points': [
            {'x': 0.15, 'y': 0.40},
            {'x': 0.15, 'y': 0.25},
            {'x': 0.05, 'y': 0.25},
            {'x': 0.05, 'y': 0.35},
          ],
          'closed': false,
          'smooth': true,
        },
        durationMs: 400,
        delayMs: 100,
        strokeWidth: 2.0,
      ),
      // Step 3: Bronchial tree (smaller branches)
      TemplateStep(
        id: 'bronchial_tree',
        action: TemplateDrawAction.drawPath,
        geometry: {
          'points': [
            {'x': 0.38, 'y': 0.35},
            {'x': 0.48, 'y': 0.30},
            {'x': 0.48, 'y': 0.45},
            {'x': 0.38, 'y': 0.50},
          ],
          'closed': false,
          'smooth': true,
        },
        durationMs: 600,
        delayMs: 150,
        strokeWidth: 1.5,
      ),
      TemplateStep(
        id: 'bronchial_tree_small',
        action: TemplateDrawAction.drawPath,
        geometry: {
          'points': [
            {'x': 0.02, 'y': 0.35},
            {'x': 0.12, 'y': 0.30},
            {'x': 0.12, 'y': 0.45},
            {'x': 0.02, 'y': 0.50},
          ],
          'closed': false,
          'smooth': true,
        },
        durationMs: 600,
        delayMs: 150,
        strokeWidth: 1.5,
      ),
      // Step 4: Alveoli region (cloud of small circles)
      TemplateStep(
        id: 'alveoli',
        action: TemplateDrawAction.drawShape,
        geometry: {
          'shape': 'circle',
          'position': {'x': 0.25, 'y': 0.55},
          'size': {'w': 0.12, 'h': 0.12},
          'fillColor': 'secondary',
        },
        durationMs: 500,
        delayMs: 200,
        strokeWidth: 1.0,
      ),
      // Step 5: Labels fade in
      TemplateStep(
        id: 'labels',
        action: TemplateDrawAction.fadeInLabel,
        durationMs: 200,
        delayMs: 100,
        strokeWidth: 0.0,
      ),
    ],
    labels: {
      'trachea_label': TemplateLabel(
        id: 'trachea_label',
        text: 'Trachea',
        position: const BoardPoint(0.22, 0.68),
        targetId: 'trachea',
        style: 'label',
      ),
      'bronchi_label': TemplateLabel(
        id: 'bronchi_label',
        text: 'Bronchi',
        position: const BoardPoint(0.48, 0.38),
        targetId: 'primary_bronchi',
        style: 'label',
      ),
      'alveoli_label': TemplateLabel(
        id: 'alveoli_label',
        text: 'Alveoli',
        position: const BoardPoint(0.30, 0.62),
        targetId: 'alveoli',
        style: 'caption',
      ),
    },
    animationMetadata: const {
      'easing': 'easeOutCubic',
      'overallDurationMs': 3300,
    },
  );
}