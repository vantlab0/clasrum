import 'dart:ui' show Color;

import '../../model/board_geometry.dart';
import '../template_definition.dart';

/// Simple machines template (physics).
///
/// Draws a lever with fulcrum, effort, and load. Progressive drawing shows
/// the lever setup first, then the mechanical advantage explanation.
class SimpleMachinesTemplate extends Template {
  SimpleMachinesTemplate() : super(
    id: 'simple_machines',
    category: TemplateCategory.physics,
    displayName: 'Simple Machines',
    defaultSize: const BoardSize(0.28, 0.20),
    steps: const [
      // Step 1: Lever beam
      TemplateStep(
        id: 'lever_beam',
        action: TemplateDrawAction.drawLine,
        geometry: {
          'start': {'x': 0.10, 'y': 0.40},
          'end': {'x': 0.75, 'y': 0.40},
        },
        durationMs: 400,
        strokeWidth: 2.5,
      ),
      // Step 2: Fulcrum (pivot point under lever)
      TemplateStep(
        id: 'fulcrum',
        action: TemplateDrawAction.drawShape,
        geometry: {
          'shape': 'circle',
          'position': {'x': 0.40, 'y': 0.30},
          'size': {'w': 0.06, 'h': 0.06},
          'fillColor': 'primary',
        },
        durationMs: 300,
        delayMs: 100,
        strokeWidth: 2.0,
      ),
      // Step 3: Load (box on left side of fulcrum)
      TemplateStep(
        id: 'load',
        action: TemplateDrawAction.drawShape,
        geometry: {
          'shape': 'rect',
          'position': {'x': 0.15, 'y': 0.45},
          'size': {'w': 0.12, 'h': 0.08},
        },
        durationMs: 400,
        delayMs: 100,
        strokeWidth: 2.0,
      ),
      // Step 4: Effort (box on right side of fulcrum)
      TemplateStep(
        id: 'effort',
        action: TemplateDrawAction.drawShape,
        geometry: {
          'shape': 'rect',
          'position': {'x': 0.55, 'y': 0.45},
          'size': {'w': 0.12, 'h': 0.08},
        },
        durationMs: 400,
        delayMs: 100,
        strokeWidth: 2.0,
      ),
      // Step 5: Label showing mechanical advantage
      TemplateStep(
        id: 'ma_label',
        action: TemplateDrawAction.drawText,
        geometry: {
          'text': 'MA = 2',
          'position': {'x': 0.25, 'y': 0.55},
          'style': 'label',
        },
        durationMs: 300,
        delayMs: 100,
        strokeWidth: 0.0,
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
      'fulcrum_label': TemplateLabel(
        id: 'fulcrum_label',
        text: 'Fulcrum',
        position: const BoardPoint(0.42, 0.25),
        style: 'label',
      ),
      'load_label': TemplateLabel(
        id: 'load_label',
        text: 'Load',
        position: const BoardPoint(0.20, 0.50),
        style: 'caption',
      ),
      'effort_label': TemplateLabel(
        id: 'effort_label',
        text: 'Effort',
        position: const BoardPoint(0.60, 0.50),
        style: 'caption',
      ),
      'ma_label': TemplateLabel(
        id: 'ma_label',
        text: 'Mechanical Advantage',
        position: const BoardPoint(0.25, 0.60),
        style: 'caption',
      ),
    },
    animationMetadata: const {
      'easing': 'easeOutCubic',
      'overallDurationMs': 2300,
    },
  );
}