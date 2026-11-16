import 'dart:ui' show Color;

import '../../model/board_geometry.dart';
import '../template_definition.dart';

/// Ribosome template (biology).
///
/// Draws a ribosome with large and small subunits and mRNA exit tunnel.
/// Progressive drawing shows the subunits first, then the rRNA structure.
class RibosomeTemplate extends Template {
  RibosomeTemplate() : super(
    id: 'ribosome',
    category: TemplateCategory.biology,
    displayName: 'Ribosome',
    defaultSize: const BoardSize(0.24, 0.20),
    steps: const [
      // Step 1: Small subunit (head)
      TemplateStep(
        id: 'small_subunit',
        action: TemplateDrawAction.drawShape,
        geometry: {
          'shape': 'circle',
          'position': {'x': 0.12, 'y': 0.20},
          'size': {'w': 0.10, 'h': 0.10},
          'fillColor': 'primary',
        },
        durationMs: 500,
        strokeWidth: 2.5,
      ),
      // Step 2: Large subunit (base)
      TemplateStep(
        id: 'large_subunit',
        action: TemplateDrawAction.drawShape,
        geometry: {
          'shape': 'circle',
          'position': {'x': 0.12, 'y': 0.20},
          'size': {'w': 0.14, 'h': 0.12},
          'fillColor': Color(0xFF2A2A2A),
        },
        durationMs: 500,
        delayMs: 200,
        strokeWidth: 2.5,
      ),
      // Step 3: mRNA exit tunnel
      TemplateStep(
        id: 'mrna_tunnel',
        action: TemplateDrawAction.drawPath,
        geometry: {
          'points': [
            {'x': 0.17, 'y': 0.25},
            {'x': 0.22, 'y': 0.20},
            {'x': 0.27, 'y': 0.25},
            {'x': 0.32, 'y': 0.35},
            {'x': 0.27, 'y': 0.45},
            {'x': 0.22, 'y': 0.50},
          ],
          'closed': false,
          'smooth': true,
        },
        durationMs: 400,
        delayMs: 100,
        strokeWidth: 1.5,
      ),
      // Step 4: rRNA rings (internal structure)
      TemplateStep(
        id: 'rrna_rings',
        action: TemplateDrawAction.drawShape,
        geometry: {
          'shape': 'circle',
          'position': {'x': 0.16, 'y': 0.24},
          'size': {'w': 0.04, 'h': 0.04},
          'fillColor': 'secondary',
        },
        durationMs: 300,
        delayMs: 100,
        strokeWidth: 0.0,
      ),
      TemplateStep(
        id: 'rrna_rings2',
        action: TemplateDrawAction.drawShape,
        geometry: {
          'shape': 'circle',
          'position': {'x': 0.20, 'y': 0.28},
          'size': {'w': 0.04, 'h': 0.04},
          'fillColor': 'secondary',
        },
        durationMs: 300,
        delayMs: 100,
        strokeWidth: 0.0,
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
      'small_subunit_label': TemplateLabel(
        id: 'small_subunit_label',
        text: 'Small subunit',
        position: const BoardPoint(0.24, 0.15),
        targetId: 'small_subunit',
        style: 'label',
      ),
      'large_subunit_label': TemplateLabel(
        id: 'large_subunit_label',
        text: 'Large subunit',
        position: const BoardPoint(0.24, 0.35),
        targetId: 'large_subunit',
        style: 'label',
      ),
      'mrna_label': TemplateLabel(
        id: 'mrna_label',
        text: 'mRNA exit',
        position: const BoardPoint(0.38, 0.30),
        targetId: 'mrna_tunnel',
        style: 'caption',
      ),
    },
    animationMetadata: const {
      'easing': 'easeOutCubic',
      'overallDurationMs': 2500,
    },
  );
}