import 'dart:ui' show Color;

import '../../model/board_geometry.dart';
import '../template_definition.dart';

/// Number line template (mathematics).
///
/// Draws a number line with markers and labels. Progressive drawing reveals
/// from zero outward in both directions.
class NumberLineTemplate extends Template {
  NumberLineTemplate() : super(
    id: 'number_line',
    category: TemplateCategory.mathematics,
    displayName: 'Number Line',
    defaultSize: const BoardSize(0.30, 0.14),
    steps: const [
      // Step 1: Number line baseline
      TemplateStep(
        id: 'baseline',
        action: TemplateDrawAction.drawLine,
        geometry: {
          'start': {'x': 0.10, 'y': 0.45},
          'end': {'x': 0.70, 'y': 0.45},
        },
        durationMs: 400,
        strokeWidth: 2.0,
      ),
      // Step 2: Zero marker
      TemplateStep(
        id: 'zero_marker',
        action: TemplateDrawAction.drawShape,
        geometry: {
          'shape': 'circle',
          'position': {'x': 0.20, 'y': 0.40},
          'size': {'w': 0.02, 'h': 0.02},
          'fillColor': 'primary',
        },
        durationMs: 300,
        delayMs: 100,
        strokeWidth: 1.5,
      ),
      // Step 3: Positive marker (1)
      TemplateStep(
        id: 'positive_1',
        action: TemplateDrawAction.drawShape,
        geometry: {
          'shape': 'circle',
          'position': {'x': 0.35, 'y': 0.40},
          'size': {'w': 0.02, 'h': 0.02},
          'fillColor': 'primary',
        },
        durationMs: 300,
        delayMs: 100,
        strokeWidth: 1.5,
      ),
      // Step 4: Negative marker (-1)
      TemplateStep(
        id: 'negative_1',
        action: TemplateDrawAction.drawShape,
        geometry: {
          'shape': 'circle',
          'position': {'x': 0.05, 'y': 0.40},
          'size': {'w': 0.02, 'h': 0.02},
          'fillColor': 'primary',
        },
        durationMs: 300,
        delayMs: 100,
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
      'zero_label': TemplateLabel(
        id: 'zero_label',
        text: '0',
        position: const BoardPoint(0.22, 0.48),
        style: 'label',
      ),
      'positive_label': TemplateLabel(
        id: 'positive_label',
        text: '1',
        position: const BoardPoint(0.38, 0.48),
        style: 'caption',
      ),
      'negative_label': TemplateLabel(
        id: 'negative_label',
        text: '-1',
        position: const BoardPoint(0.08, 0.48),
        style: 'caption',
      ),
      'direction_label': TemplateLabel(
        id: 'direction_label',
        text: '→',
        position: const BoardPoint(0.72, 0.45),
        style: 'caption',
      ),
    },
    animationMetadata: const {
      'easing': 'easeOutCubic',
      'overallDurationMs': 1800,
    },
  );
}