import 'dart:ui' show Color;

import '../../model/board_geometry.dart';
import '../template_definition.dart';

/// Coordinate axes template (mathematics).
///
/// Draws X and Y axes with arrowheads and origin label.
/// Progressive drawing shows axes first, then the origin mark.
class CoordinateAxesTemplate extends Template {
  CoordinateAxesTemplate() : super(
    id: 'coordinate_axes',
    category: TemplateCategory.mathematics,
    displayName: 'Coordinate Axes',
    defaultSize: const BoardSize(0.28, 0.26),
    steps: const [
      // Step 1: X axis
      TemplateStep(
        id: 'x_axis',
        action: TemplateDrawAction.drawArrow,
        geometry: {
          'start': {'x': 0.05, 'y': 0.14},
          'end': {'x': 0.31, 'y': 0.14},
          'label': 'x',
        },
        durationMs: 600,
        strokeWidth: 2.0,
      ),
      // Step 2: Y axis
      TemplateStep(
        id: 'y_axis',
        action: TemplateDrawAction.drawArrow,
        geometry: {
          'start': {'x': 0.05, 'y': 0.38},
          'end': {'x': 0.05, 'y': 0.12},
          'label': 'y',
        },
        durationMs: 600,
        delayMs: 150,
        labelId: 'origin_label',
        strokeWidth: 2.0,
      ),
      // Step 3: Origin dot
      TemplateStep(
        id: 'origin',
        action: TemplateDrawAction.drawShape,
        geometry: {
          'shape': 'circle',
          'position': {'x': 0.043, 'y': 0.132},
          'size': {'w': 0.014, 'h': 0.014},
          'fillColor': 'primary',
        },
        durationMs: 300,
        delayMs: 100,
        strokeWidth: 1.0,
      ),
    ],
    labels: {
      'origin_label': TemplateLabel(
        id: 'origin_label',
        text: 'O(0,0)',
        position: const BoardPoint(0.01, 0.145),
        targetId: 'origin',
        style: 'caption',
      ),
    },
    animationMetadata: const {
      'easing': 'easeOutCubic',
      'overallDurationMs': 1450,
    },
  );
}
