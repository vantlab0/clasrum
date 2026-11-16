import 'dart:ui' show Color;

import '../../model/board_geometry.dart';
import '../template_definition.dart';

/// Mitochondrion template (biology).
///
/// Draws an outer membrane ellipse, an inner membrane with cristae folds,
/// and labels for each major part. Progressive drawing reveals the structure
/// from outside to inside, matching how a teacher would explain it.
class MitochondriaTemplate extends Template {
  MitochondriaTemplate() : super(
    id: 'mitochondria',
    category: TemplateCategory.biology,
    displayName: 'Mitochondrion',
    defaultSize: const BoardSize(0.26, 0.22),
    steps: const [
      // Step 1: Outer membrane ellipse
      TemplateStep(
        id: 'outer_membrane',
        action: TemplateDrawAction.drawShape,
        geometry: {
          'shape': 'ellipse',
          'position': {'x': 0.08, 'y': 0.20},
          'size': {'w': 0.18, 'h': 0.14},
        },
        durationMs: 700,
        strokeWidth: 2.5,
      ),
      // Step 2: Inner membrane (wavy line inside outer membrane)
      TemplateStep(
        id: 'inner_membrane',
        action: TemplateDrawAction.drawPath,
        geometry: {
          'points': [
            {'x': 0.10, 'y': 0.27},
            {'x': 0.13, 'y': 0.24},
            {'x': 0.16, 'y': 0.28},
            {'x': 0.19, 'y': 0.25},
            {'x': 0.22, 'y': 0.29},
            {'x': 0.25, 'y': 0.26},
          ],
          'closed': false,
          'smooth': true,
        },
        durationMs: 800,
        delayMs: 150,
        strokeWidth: 2.0,
      ),
      // Step 3: Cristae (folded inner membrane structures)
      TemplateStep(
        id: 'cristae',
        action: TemplateDrawAction.drawPath,
        geometry: {
          'points': [
            {'x': 0.12, 'y': 0.30},
            {'x': 0.14, 'y': 0.27},
            {'x': 0.14, 'y': 0.33},
            {'x': 0.17, 'y': 0.30},
            {'x': 0.17, 'y': 0.34},
            {'x': 0.20, 'y': 0.31},
            {'x': 0.20, 'y': 0.35},
            {'x': 0.23, 'y': 0.32},
          ],
          'closed': false,
          'smooth': true,
        },
        durationMs: 900,
        delayMs: 250,
        labelId: 'cristae_label',
        strokeWidth: 1.5,
      ),
      // Step 4: Matrix fill hint (subtle inner shading)
      TemplateStep(
        id: 'matrix',
        action: TemplateDrawAction.drawShape,
        geometry: {
          'shape': 'ellipse',
          'position': {'x': 0.11, 'y': 0.23},
          'size': {'w': 0.16, 'h': 0.10},
          'fillColor': 'primary',
        },
        durationMs: 400,
        delayMs: 100,
        strokeWidth: 0.0,
      ),
    ],
    labels: {
      'outer_membrane_label': TemplateLabel(
        id: 'outer_membrane_label',
        text: 'Outer membrane',
        position: const BoardPoint(0.30, 0.22),
        targetId: 'outer_membrane',
        style: 'label',
      ),
      'inner_membrane_label': TemplateLabel(
        id: 'inner_membrane_label',
        text: 'Inner membrane',
        position: const BoardPoint(0.02, 0.40),
        targetId: 'inner_membrane',
        style: 'label',
      ),
      'cristae_label': TemplateLabel(
        id: 'cristae_label',
        text: 'Cristae',
        position: const BoardPoint(0.26, 0.38),
        targetId: 'cristae',
        style: 'label',
      ),
      'matrix_label': TemplateLabel(
        id: 'matrix_label',
        text: 'Matrix',
        position: const BoardPoint(0.18, 0.30),
        targetId: 'matrix',
        style: 'caption',
      ),
    },
    animationMetadata: const {
      'easing': 'easeOutCubic',
      'overallDurationMs': 2800,
    },
  );
}
