import 'dart:ui' show Color;

import '../../model/board_geometry.dart';
import '../template_definition.dart';

/// Graph template (mathematics).
///
/// Draws coordinate axes with labeled quadrants. Progressive drawing shows
/// the axes first, then the origin, then grid lines.
class GraphTemplate extends Template {
  GraphTemplate() : super(
    id: 'graph',
    category: TemplateCategory.mathematics,
    displayName: 'Graph',
    defaultSize: const BoardSize(0.30, 0.26),
    steps: const [
      // Step 1: X axis with arrowhead
      TemplateStep(
        id: 'x_axis',
        action: TemplateDrawAction.drawArrow,
        geometry: {
          'start': {'x': 0.10, 'y': 0.45},
          'end': {'x': 0.70, 'y': 0.45},
          'label': 'x',
        },
        durationMs: 500,
        strokeWidth: 2.0,
      ),
      // Step 2: Y axis with arrowhead
      TemplateStep(
        id: 'y_axis',
        action: TemplateDrawAction.drawArrow,
        geometry: {
          'start': {'x': 0.10, 'y': 0.10},
          'end': {'x': 0.10, 'y': 0.55},
          'label': 'y',
        },
        durationMs: 500,
        delayMs: 150,
        strokeWidth: 2.0,
        labelId: 'origin_label',
      ),
      // Step 3: Origin dot
      TemplateStep(
        id: 'origin',
        action: TemplateDrawAction.drawShape,
        geometry: {
          'shape': 'circle',
          'position': {'x': 0.10, 'y': 0.43},
          'size': {'w': 0.014, 'h': 0.014},
          'fillColor': 'primary',
        },
        durationMs: 300,
        delayMs: 100,
        strokeWidth: 1.0,
      ),
      // Step 4: Grid lines (minor)
      TemplateStep(
        id: 'grid_minor',
        action: TemplateDrawAction.drawPath,
        geometry: {
          'points': [
            {'x': 0.10, 'y': 0.10},
            {'x': 0.10, 'y': 0.55},
            {'x': 0.70, 'y': 0.55},
            {'x': 0.70, 'y': 0.10},
            {'x': 0.10, 'y': 0.10},
          ],
          'closed': true,
          'smooth': false,
        },
        durationMs: 500,
        delayMs: 200,
        strokeWidth: 0.5,
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
      'origin_label': TemplateLabel(
        id: 'origin_label',
        text: 'O(0,0)',
        position: const BoardPoint(0.08, 0.50),
        targetId: 'origin',
        style: 'caption',
      ),
      'x_label': TemplateLabel(
        id: 'x_label',
        text: 'x',
        position: const BoardPoint(0.75, 0.47),
        style: 'label',
      ),
      'y_label': TemplateLabel(
        id: 'y_label',
        text: 'y',
        position: const BoardPoint(0.08, 0.55),
        style: 'label',
      ),
      'quadrant_1': TemplateLabel(
        id: 'quadrant_1',
        text: 'QI',
        position: const BoardPoint(0.45, 0.30),
        style: 'caption',
      ),
      'quadrant_2': TemplateLabel(
        id: 'quadrant_2',
        text: 'QII',
        position: const BoardPoint(0.20, 0.30),
        style: 'caption',
      ),
    },
    animationMetadata: const {
      'easing': 'easeOutCubic',
      'overallDurationMs': 2600,
    },
  );
}