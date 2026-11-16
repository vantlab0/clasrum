import 'dart:ui' show Color;

import '../../model/board_geometry.dart';
import '../template_definition.dart';

/// Neuron template (biology).
///
/// Draws a neuron with cell body, dendrites, and axon. Progressive drawing
/// reveals from the cell body outward, matching how a teacher would explain
/// neural signaling.
class NeuronTemplate extends Template {
  NeuronTemplate() : super(
    id: 'neuron',
    category: TemplateCategory.biology,
    displayName: 'Neuron',
    defaultSize: const BoardSize(0.30, 0.30),
    steps: const [
      // Step 1: Cell body (soma) - large ellipse
      TemplateStep(
        id: 'cell_body',
        action: TemplateDrawAction.drawShape,
        geometry: {
          'shape': 'ellipse',
          'position': {'x': 0.10, 'y': 0.15},
          'size': {'w': 0.20, 'h': 0.16},
          'fillColor': 'primary',
        },
        durationMs: 600,
        strokeWidth: 2.5,
      ),
      // Step 2: Dendrites (branching lines left)
      TemplateStep(
        id: 'dendrites_left',
        action: TemplateDrawAction.drawPath,
        geometry: {
          'points': [
            {'x': 0.15, 'y': 0.30},
            {'x': 0.05, 'y': 0.35},
            {'x': 0.02, 'y': 0.40},
            {'x': 0.08, 'y': 0.45},
          ],
          'closed': false,
          'smooth': true,
        },
        durationMs: 700,
        delayMs: 200,
        strokeWidth: 2.0,
      ),
      TemplateStep(
        id: 'dendrites_right',
        action: TemplateDrawAction.drawPath,
        geometry: {
          'points': [
            {'x': 0.25, 'y': 0.30},
            {'x': 0.35, 'y': 0.35},
            {'x': 0.38, 'y': 0.40},
            {'x': 0.32, 'y': 0.45},
          ],
          'closed': false,
          'smooth': true,
        },
        durationMs: 700,
        delayMs: 200,
        strokeWidth: 2.0,
      ),
      // Step 3: Axon (long line down)
      TemplateStep(
        id: 'axon',
        action: TemplateDrawAction.drawPath,
        geometry: {
          'points': [
            {'x': 0.20, 'y': 0.31},
            {'x': 0.20, 'y': 0.70},
          ],
          'closed': false,
          'smooth': false,
        },
        durationMs: 500,
        delayMs: 200,
        strokeWidth: 3.0,
      ),
      // Step 4: Terminal branches (axon terminal)
      TemplateStep(
        id: 'terminal_branches',
        action: TemplateDrawAction.drawPath,
        geometry: {
          'points': [
            {'x': 0.18, 'y': 0.68},
            {'x': 0.15, 'y': 0.72},
            {'x': 0.25, 'y': 0.72},
          ],
          'closed': false,
          'smooth': true,
        },
        durationMs: 400,
        delayMs: 100,
        strokeWidth: 1.5,
      ),
      // Step 5: Highlight energy area
      TemplateStep(
        id: 'highlight',
        action: TemplateDrawAction.highlight,
        geometry: {'targetIds': ['cell_body']},
        durationMs: 300,
        delayMs: 100,
        strokeWidth: 0.0,
      ),
    ],
    labels: {
      'cell_body_label': TemplateLabel(
        id: 'cell_body_label',
        text: 'Cell body (soma)',
        position: const BoardPoint(0.32, 0.18),
        targetId: 'cell_body',
        style: 'label',
      ),
      'axon_label': TemplateLabel(
        id: 'axon_label',
        text: 'Axon',
        position: const BoardPoint(0.22, 0.75),
        targetId: 'axon',
        style: 'label',
      ),
      'dendrite_label': TemplateLabel(
        id: 'dendrite_label',
        text: 'Dendrites',
        position: const BoardPoint(0.05, 0.38),
        targetId: 'dendrites_left',
        style: 'label',
      ),
    },
    animationMetadata: const {
      'easing': 'easeOutCubic',
      'overallDurationMs': 3600,
    },
  );
}