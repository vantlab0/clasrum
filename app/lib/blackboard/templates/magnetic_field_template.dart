import 'dart:ui' show Color;

import '../../model/board_geometry.dart';
import '../template_definition.dart';

/// Magnetic field template (physics).
///
/// Draws a magnetic field with a bar magnet and field lines. Progressive drawing
/// shows the magnet first, then the field lines looping around.
class MagneticFieldTemplate extends Template {
  MagneticFieldTemplate() : super(
    id: 'magnetic_field',
    category: TemplateCategory.physics,
    displayName: 'Magnetic Field',
    defaultSize: const BoardSize(0.26, 0.20),
    steps: const [
      // Step 1: Bar magnet
      TemplateStep(
        id: 'magnet',
        action: TemplateDrawAction.drawShape,
        geometry: {
          'shape': 'rect',
          'position': {'x': 0.12, 'y': 0.25},
          'size': {'w': 0.14, 'h': 0.10},
          'fillColor': 'primary',
        },
        durationMs: 500,
        strokeWidth: 2.5,
      ),
      // Step 2: North pole indicator
      TemplateStep(
        id: 'north_pole',
        action: TemplateDrawAction.drawShape,
        geometry: {
          'shape': 'circle',
          'position': {'x': 0.12, 'y': 0.20},
          'size': {'w': 0.03, 'h': 0.03},
          'fillColor': 'yellow',
        },
        durationMs: 300,
        delayMs: 100,
        strokeWidth: 0.0,
      ),
      // Step 3: South pole indicator
      TemplateStep(
        id: 'south_pole',
        action: TemplateDrawAction.drawShape,
        geometry: {
          'shape': 'circle',
          'position': {'x': 0.12 + 0.14, 'y': 0.25 + 0.10},
          'size': {'w': 0.03, 'h': 0.03},
          'fillColor': 'yellow',
        },
        durationMs: 300,
        delayMs: 100,
        strokeWidth: 0.0,
      ),
      // Step 4: Field lines (curved arches from N to S)
      TemplateStep(
        id: 'field_line_top',
        action: TemplateDrawAction.drawPath,
        geometry: {
          'points': [
            {'x': 0.12, 'y': 0.20},
            {'x': 0.22, 'y': 0.10},
            {'x': 0.32, 'y': 0.20},
          ],
          'closed': false,
          'smooth': true,
        },
        durationMs: 400,
        delayMs: 50,
        strokeWidth: 1.5,
      ),
      TemplateStep(
        id: 'field_line_middle',
        action: TemplateDrawAction.drawPath,
        geometry: {
          'points': [
            {'x': 0.12, 'y': 0.30},
            {'x': 0.22, 'y': 0.40},
            {'x': 0.32, 'y': 0.30},
          ],
          'closed': false,
          'smooth': true,
        },
        durationMs: 400,
        delayMs: 50,
        strokeWidth: 1.5,
      ),
      TemplateStep(
        id: 'field_line_bottom',
        action: TemplateDrawAction.drawPath,
        geometry: {
          'points': [
            {'x': 0.12, 'y': 0.40},
            {'x': 0.22, 'y': 0.50},
            {'x': 0.32, 'y': 0.40},
          ],
          'closed': false,
          'smooth': true,
        },
        durationMs: 400,
        delayMs: 50,
        strokeWidth: 1.5,
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
      'magnet_label': TemplateLabel(
        id: 'magnet_label',
        text: 'Magnet',
        position: const BoardPoint(0.14, 0.45),
        targetId: 'magnet',
        style: 'label',
      ),
      'north_label': TemplateLabel(
        id: 'north_label',
        text: 'North',
        position: const BoardPoint(0.10, 0.18),
        style: 'caption',
      ),
      'south_label': TemplateLabel(
        id: 'south_label',
        text: 'South',
        position: const BoardPoint(0.23, 0.33),
        style: 'caption',
      ),
      'field_line_label': TemplateLabel(
        id: 'field_line_label',
        text: 'Field lines',
        position: const BoardPoint(0.18, 0.28),
        style: 'caption',
      ),
    },
    animationMetadata: const {
      'easing': 'easeOutCubic',
      'overallDurationMs': 2600,
    },
  );
}