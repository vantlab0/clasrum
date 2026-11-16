import 'dart:ui' show Color;

import '../../model/board_geometry.dart';
import '../template_definition.dart';

/// Geometric shapes template (mathematics).
///
/// Draws a circle, square, and triangle with shared center. Progressive drawing
/// reveals each shape in sequence.
class GeometricShapesTemplate extends Template {
  GeometricShapesTemplate() : super(
    id: 'geometric_shapes',
    category: TemplateCategory.mathematics,
    displayName: 'Geometric Shapes',
    defaultSize: const BoardSize(0.24, 0.20),
    steps: const [
      // Step 1: Circle (outermost)
      TemplateStep(
        id: 'circle',
        action: TemplateDrawAction.drawShape,
        geometry: {
          'shape': 'circle',
          'position': {'x': 0.12, 'y': 0.10},
          'size': {'w': 0.16, 'h': 0.16},
          'fillColor': 'transparent',
        },
        durationMs: 500,
        strokeWidth: 2.5,
      ),
      // Step 2: Square (inscribed)
      TemplateStep(
        id: 'square',
        action: TemplateDrawAction.drawShape,
        geometry: {
          'shape': 'rect',
          'position': {'x': 0.16, 'y': 0.14},
          'size': {'w': 0.12, 'h': 0.12},
        },
        durationMs: 500,
        delayMs: 150,
        strokeWidth: 2.0,
      ),
      // Step 3: Triangle (inscribed)
      TemplateStep(
        id: 'triangle',
        action: TemplateDrawAction.drawPath,
        geometry: {
          'points': [
            {'x': 0.18, 'y': 0.10},
            {'x': 0.28, 'y': 0.24},
            {'x': 0.08, 'y': 0.24},
          ],
          'closed': true,
          'smooth': true,
        },
        durationMs: 500,
        delayMs: 150,
        strokeWidth: 2.0,
      ),
      // Step 4: Center point
      TemplateStep(
        id: 'center',
        action: TemplateDrawAction.drawShape,
        geometry: {
          'shape': 'circle',
          'position': {'x': 0.18, 'y': 0.16},
          'size': {'w': 0.02, 'h': 0.02},
          'fillColor': 'primary',
        },
        durationMs: 300,
        delayMs: 100,
        strokeWidth: 1.0,
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
      'circle_label': TemplateLabel(
        id: 'circle_label',
        text: 'Circle',
        position: const BoardPoint(0.30, 0.12),
        style: 'label',
      ),
      'square_label': TemplateLabel(
        id: 'square_label',
        text: 'Square',
        position: const BoardPoint(0.30, 0.20),
        style: 'caption',
      ),
      'triangle_label': TemplateLabel(
        id: 'triangle_label',
        text: 'Triangle',
        position: const BoardPoint(0.30, 0.28),
        style: 'caption',
      ),
      'center_label': TemplateLabel(
        id: 'center_label',
        text: 'Center',
        position: const BoardPoint(0.22, 0.18),
        style: 'caption',
      ),
    },
    animationMetadata: const {
      'easing': 'easeOutCubic',
      'overallDurationMs': 2400,
    },
  );
}