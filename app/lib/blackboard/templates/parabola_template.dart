import 'dart:ui' show Color;

import '../../model/board_geometry.dart';
import '../template_definition.dart';

/// Parabola template (mathematics).
///
/// Draws a parabolic arc with vertex and focus. Progressive drawing reveals
/// from the vertex out to the arms of the parabola.
class ParabolaTemplate extends Template {
  ParabolaTemplate() : super(
    id: 'parabola',
    category: TemplateCategory.mathematics,
    displayName: 'Parabola',
    defaultSize: const BoardSize(0.28, 0.18),
    steps: const [
      // Step 1: Vertex point
      TemplateStep(
        id: 'vertex',
        action: TemplateDrawAction.drawShape,
        geometry: {
          'shape': 'circle',
          'position': {'x': 0.20, 'y': 0.45},
          'size': {'w': 0.02, 'h': 0.02},
          'fillColor': 'primary',
        },
        durationMs: 300,
        strokeWidth: 2.0,
      ),
      // Step 2: Vertical axis of symmetry
      TemplateStep(
        id: 'axis_sym',
        action: TemplateDrawAction.drawLine,
        geometry: {
          'start': {'x': 0.20, 'y': 0.45},
          'end': {'x': 0.20, 'y': 0.15},
        },
        durationMs: 400,
        delayMs: 100,
        strokeWidth: 1.5,
      ),
      // Step 3: Parabolic arc (opening upward)
      TemplateStep(
        id: 'parabola_arc',
        action: TemplateDrawAction.drawPath,
        geometry: {
          'points': [
            {'x': 0.20, 'y': 0.45},
            {'x': 0.10, 'y': 0.70},
            {'x': 0.30, 'y': 0.70},
            {'x': 0.05, 'y': 0.85},
            {'x': 0.35, 'y': 0.85},
          ],
          'closed': false,
          'smooth': true,
        },
        durationMs: 500,
        delayMs: 150,
        strokeWidth: 2.0,
      ),
      // Step 4: Focus point
      TemplateStep(
        id: 'focus',
        action: TemplateDrawAction.drawShape,
        geometry: {
          'shape': 'circle',
          'position': {'x': 0.20, 'y': 0.35},
          'size': {'w': 0.02, 'h': 'h': 0.02},
          'fillColor': 'yellow',
        },
        durationMs: 300,
        delayMs: 100,
        strokeWidth: 1.5,
      ),
      // Step 5: Directrix line
      TemplateStep(
        id: 'directrix',
        action: TemplateDrawAction.drawLine,
        geometry: {
          'start': {'x': 0.10, 'y': 0.55},
          'end': {'x': 0.30, 'y': 0.55},
        },
        durationMs: 300,
        delayMs: 100,
        strokeWidth: 1.0,
      ),
      // Step 6: Labels
      TemplateStep(
        id: 'labels',
        action: TemplateDrawAction.fadeInLabel,
        durationMs: 200,
        delayMs: 100,
        strokeWidth: 0.0,
      ),
    ],
    labels: {
      'vertex_label': TemplateLabel(
        id: 'vertex_label',
        text: 'Vertex',
        position: const BoardPoint(0.22, 0.50),
        style: 'label',
      ),
      'focus_label': TemplateLabel(
        id: 'focus_label',
        text: 'Focus',
        position: const BoardPoint(0.22, 0.38),
        style: 'caption',
      ),
      'directrix_label': TemplateLabel(
        id: 'directrix_label',
        text: 'Directrix',
        position: const BoardPoint(0.20, 0.58),
        style: 'caption',
      ),
      'axis_label': TemplateLabel(
        id: 'axis_label',
        text: 'Axis of symmetry',
        position: const BoardPoint(0.15, 0.30),
        style: 'caption',
      ),
    },
    animationMetadata: const {
      'easing': 'easeOutCubic',
      'overallDurationMs': 2200,
    },
  );
}