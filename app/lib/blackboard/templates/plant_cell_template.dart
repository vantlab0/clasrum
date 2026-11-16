import 'dart:ui' show Color;

import '../../model/board_geometry.dart';
import '../template_definition.dart';

/// Plant cell template (biology).
///
/// Draws a plant cell with cell wall, chloroplasts, and central vacuole.
/// Progressive drawing shows the outer boundary first, then internal structures.
class PlantCellTemplate extends Template {
  PlantCellTemplate() : super(
    id: 'plant_cell',
    category: TemplateCategory.biology,
    displayName: 'Plant Cell',
    defaultSize: const BoardSize(0.32, 0.28),
    steps: const [
      // Step 1: Cell wall (rectangle with rounded corners)
      TemplateStep(
        id: 'cell_wall',
        action: TemplateDrawAction.drawShape,
        geometry: {
          'shape': 'rect',
          'position': {'x': 0.04, 'y': 0.06},
          'size': {'w': 0.28, 'h': 0.24},
        },
        durationMs: 700,
        strokeWidth: 3.0,
      ),
      // Step 2: Central vacuole (large circle)
      TemplateStep(
        id: 'vacuole',
        action: TemplateDrawAction.drawShape,
        geometry: {
          'shape': 'circle',
          'position': {'x': 0.18, 'y': 0.25},
          'size': {'w': 0.14, 'h': 0.14},
          'fillColor': 'secondary',
        },
        durationMs: 500,
        delayMs: 200,
        strokeWidth: 1.5,
      ),
      // Step 3: Chloroplasts (multiple small ellipses)
      TemplateStep(
        id: 'chloroplasts',
        action: TemplateDrawAction.drawShape,
        geometry: {
          'shape': 'ellipse',
          'position': {'x': 0.08, 'y': 0.15},
          'size': {'w': 0.06, 'h': 0.08},
        },
        durationMs: 400,
        delayMs: 100,
        strokeWidth: 1.0,
      ),
      TemplateStep(
        id: 'chloroplasts2',
        action: TemplateDrawAction.drawShape,
        geometry: {
          'shape': 'ellipse',
          'position': {'x': 0.28, 'y': 0.18},
          'size': {'w': 0.06, 'h': 0.08},
        },
        durationMs: 400,
        delayMs: 100,
        strokeWidth: 1.0,
      ),
      TemplateStep(
        id: 'chloroplasts3',
        action: TemplateDrawAction.drawShape,
        geometry: {
          'shape': 'ellipse',
          'position': {'x': 0.18, 'y': 0.40},
          'size': {'w': 0.06, 'h': 0.08},
        },
        durationMs: 400,
        delayMs: 100,
        strokeWidth: 1.0,
      ),
      // Step 4: Nucleus
      TemplateStep(
        id: 'nucleus',
        action: TemplateDrawAction.drawShape,
        geometry: {
          'shape': 'circle',
          'position': {'x': 0.22, 'y': 0.35},
          'size': {'w': 0.06, 'h': 0.06},
          'fillColor': 'primary',
        },
        durationMs: 500,
        delayMs: 150,
        strokeWidth: 2.0,
        labelId: 'nucleus_label',
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
      'cell_wall_label': TemplateLabel(
        id: 'cell_wall_label',
        text: 'Cell wall',
        position: const BoardPoint(0.35, 0.10),
        targetId: 'cell_wall',
        style: 'label',
      ),
      'vacuole_label': TemplateLabel(
        id: 'vacuole_label',
        text: 'Vacuole',
        position: const BoardPoint(0.28, 0.38),
        targetId: 'vacuole',
        style: 'label',
      ),
      'nucleus_label': TemplateLabel(
        id: 'nucleus_label',
        text: 'Nucleus',
        position: const BoardPoint(0.30, 0.42),
        targetId: 'nucleus',
        style: 'label',
      ),
      'chloroplast_label': TemplateLabel(
        id: 'chloroplast_label',
        text: 'Chloroplast',
        position: const BoardPoint(0.10, 0.18),
        targetId: 'chloroplasts',
        style: 'caption',
      ),
    },
    animationMetadata: const {
      'easing': 'easeOutCubic',
      'overallDurationMs': 3200,
    },
  );
}