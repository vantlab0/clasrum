import 'dart:ui' show Color;

import '../../model/board_geometry.dart';
import '../template_definition.dart';

/// Atomic structure template (chemistry).
///
/// Draws a nucleus at center with electron shells (ellipses) and
/// electrons (small dots) on the shells.
class AtomTemplate extends Template {
  AtomTemplate() : super(
    id: 'atom',
    category: TemplateCategory.chemistry,
    displayName: 'Atom',
    defaultSize: const BoardSize(0.24, 0.24),
    steps: const [
      // Step 1: Nucleus
      TemplateStep(
        id: 'nucleus',
        action: TemplateDrawAction.drawShape,
        geometry: {
          'shape': 'circle',
          'position': {'x': 0.10, 'y': 0.10},
          'size': {'w': 0.04, 'h': 0.04},
          'fillColor': 'red',
        },
        durationMs: 400,
        strokeWidth: 1.5,
        labelId: 'nucleus_label',
      ),
      // Step 2: Inner electron shell
      TemplateStep(
        id: 'inner_shell',
        action: TemplateDrawAction.drawShape,
        geometry: {
          'shape': 'ellipse',
          'position': {'x': 0.02, 'y': 0.02},
          'size': {'w': 0.20, 'h': 0.20},
        },
        durationMs: 500,
        delayMs: 150,
        strokeWidth: 1.5,
      ),
      // Step 3: Outer electron shell
      TemplateStep(
        id: 'outer_shell',
        action: TemplateDrawAction.drawShape,
        geometry: {
          'shape': 'ellipse',
          'position': {'x': -0.04, 'y': -0.04},
          'size': {'w': 0.32, 'h': 0.32},
        },
        durationMs: 600,
        delayMs: 250,
        labelId: 'shell_label',
        strokeWidth: 1.5,
      ),
    ],
    labels: {
      'nucleus_label': TemplateLabel(
        id: 'nucleus_label',
        text: 'Nucleus',
        position: const BoardPoint(0.16, 0.08),
        targetId: 'nucleus',
        style: 'label',
      ),
      'shell_label': TemplateLabel(
        id: 'shell_label',
        text: 'Electron shells',
        position: const BoardPoint(-0.10, -0.12),
        targetId: 'outer_shell',
        style: 'label',
      ),
    },
    animationMetadata: const {
      'easing': 'easeOutCubic',
      'overallDurationMs': 1650,
    },
  );
}
