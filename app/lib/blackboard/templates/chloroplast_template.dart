import 'dart:ui' show Color;

import '../../model/board_geometry.dart';
import '../template_definition.dart';

/// Chloroplast template (biology).
///
/// Draws a chloroplast with outer membrane and thylakoid stacks (grana).
/// Progressive drawing reveals the structure from outside to inside.
class ChloroplastTemplate extends Template {
  ChloroplastTemplate() : super(
    id: 'chloroplast',
    category: TemplateCategory.biology,
    displayName: 'Chloroplast',
    defaultSize: const BoardSize(0.22, 0.18),
    steps: const [
      // Step 1: Outer membrane (ellipse)
      TemplateStep(
        id: 'outer_membrane',
        action: TemplateDrawAction.drawShape,
        geometry: {
          'shape': 'ellipse',
          'position': {'x': 0.10, 'y': 0.10},
          'size': {'w': 0.14, 'h': 0.10},
          'fillColor': 'primary',
        },
        durationMs: 500,
        strokeWidth: 2.5,
      ),
      // Step 2: Thylakoid membrane (curved line inside)
      TemplateStep(
        id: 'thylakoid',
        action: TemplateDrawAction.drawPath,
        geometry: {
          'points': [
            {'x': 0.13, 'y': 0.15},
            {'x': 0.17, 'y': 0.12},
            {'x': 0.22, 'y': 0.15},
            {'x': 0.25, 'y': 0.20},
            {'x': 0.22, 'y': 0.25},
            {'x': 0.17, 'y': 0.28},
            {'x': 0.13, 'y': 0.25},
            {'x': 0.10, 'y': 0.20},
          ],
          'closed': true,
          'smooth': true,
        },
        durationMs: 600,
        delayMs: 150,
        strokeWidth: 1.5,
      ),
      // Step 3: Grana (small ellipses along thylakoid)
      TemplateStep(
        id: 'grana',
        action: TemplateDrawAction.drawShape,
        geometry: {
          'shape': 'circle',
          'position': {'x': 0.15, 'y': 0.18},
          'size': {'w': 0.02, 'h': 0.02},
          'fillColor': 'yellow',
        },
        durationMs: 300,
        delayMs: 50,
        strokeWidth: 0.0,
      ),
      TemplateStep(
        id: 'grana2',
        action: TemplateDrawAction.drawShape,
        geometry: {
          'shape': 'circle',
          'position': {'x': 0.22, 'y': 0.22},
          'size': {'w': 0.02, 'h': 0.02},
          'fillColor': 'yellow',
        },
        durationMs: 300,
        delayMs: 50,
        strokeWidth: 0.0,
      ),
      TemplateStep(
        id: 'grana3',
        action: TemplateDrawAction.drawShape,
        geometry: {
          'shape': 'circle',
          'position': {'x': 0.16, 'y': 0.26},
          'size': {'w': 0.02, 'h': 0.02},
          'fillColor': 'yellow',
        },
        durationMs: 300,
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
      'outer_membrane_label': TemplateLabel(
        id: 'outer_membrane_label',
        text: 'Outer membrane',
        position: const BoardPoint(0.30, 0.12),
        targetId: 'outer_membrane',
        style: 'label',
      ),
      'thylakoid_label': TemplateLabel(
        id: 'thylakoid_label',
        text: 'Thylakoid',
        position: const BoardPoint(0.28, 0.16),
        targetId: 'thylakoid',
        style: 'caption',
      ),
      'grana_label': TemplateLabel(
        id: 'grana_label',
        text: 'Grana',
        position: const BoardPoint(0.18, 0.22),
        targetId: 'grana',
        style: 'caption',
      ),
    },
    animationMetadata: const {
      'easing': 'easeOutCubic',
      'overallDurationMs': 2200,
    },
  );
}