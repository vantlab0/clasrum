import 'dart:ui' show Color;

import '../model/board_geometry.dart';
import 'template_definition.dart';

/// Animal cell template (biology).
///
/// Draws a cell membrane (outer boundary), nucleus (central circle),
/// and labels for key organelles. Progressive drawing shows the cell
/// boundary first, then internal structures.
class CellTemplate extends Template {
  CellTemplate()
      : super(
          id: 'animal_cell',
          category: TemplateCategory.biology,
          displayName: 'Animal Cell',
          defaultSize: const BoardSize(0.30, 0.28),
          steps: const [
            // Step 1: Cell membrane (outer boundary)
            TemplateStep(
              id: 'cell_membrane',
              action: TemplateDrawAction.drawShape,
              geometry: {
                'shape': 'ellipse',
                'position': {'x': 0.05, 'y': 0.10},
                'size': {'w': 0.24, 'h': 0.22},
              },
              durationMs: 800,
              strokeWidth: 3.0,
            ),
            // Step 2: Nucleus (central circle)
            TemplateStep(
              id: 'nucleus',
              action: TemplateDrawAction.drawShape,
              geometry: {
                'shape': 'circle',
                'position': {'x': 0.14, 'y': 0.22},
                'size': {'w': 0.08, 'h': 0.08},
              },
              durationMs: 600,
              delayMs: 200,
              strokeWidth: 2.5,
              labelId: 'nucleus_label',
            ),
            // Step 3: Nucleolus (inner dot in nucleus)
            TemplateStep(
              id: 'nucleolus',
              action: TemplateDrawAction.drawShape,
              geometry: {
                'shape': 'circle',
                'position': {'x': 0.165, 'y': 0.245},
                'size': {'w': 0.025, 'h': 0.025},
                'fillColor': 'secondary',
              },
              durationMs: 300,
              delayMs: 100,
              strokeWidth: 1.0,
            ),
          ],
          labels: {
            'cell_membrane_label': TemplateLabel(
              id: 'cell_membrane_label',
              text: 'Cell membrane',
              position: const BoardPoint(0.32, 0.15),
              targetId: 'cell_membrane',
              style: 'label',
            ),
            'nucleus_label': TemplateLabel(
              id: 'nucleus_label',
              text: 'Nucleus',
              position: const BoardPoint(0.02, 0.34),
              targetId: 'nucleus',
              style: 'label',
            ),
            'nucleolus_label': TemplateLabel(
              id: 'nucleolus_label',
              text: 'Nucleolus',
              position: const BoardPoint(0.20, 0.28),
              targetId: 'nucleolus',
              style: 'caption',
            ),
          },
          animationMetadata: const {
            'easing': 'easeOutCubic',
            'overallDurationMs': 1900,
          },
        );
}
