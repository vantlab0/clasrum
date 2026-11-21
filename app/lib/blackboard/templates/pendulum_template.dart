import 'dart:ui' show Color;

import '../model/board_geometry.dart';
import 'template_definition.dart';

/// Pendulum template (physics).
///
/// Draws a pendulum with pivot, rod, and bob. Progressive drawing reveals
/// from the pivot down to the swinging bob.
class PendulumTemplate extends Template {
  PendulumTemplate()
      : super(
          id: 'pendulum',
          category: TemplateCategory.physics,
          displayName: 'Pendulum',
          defaultSize: const BoardSize(0.22, 0.28),
          steps: const [
            // Step 1: Pivot point
            TemplateStep(
              id: 'pivot',
              action: TemplateDrawAction.drawShape,
              geometry: {
                'shape': 'circle',
                'position': {'x': 0.16, 'y': 0.08},
                'size': {'w': 0.03, 'h': 0.03},
                'fillColor': 'primary',
              },
              durationMs: 300,
              strokeWidth: 2.0,
            ),
            // Step 2: Rod (vertical line)
            TemplateStep(
              id: 'rod',
              action: TemplateDrawAction.drawLine,
              geometry: {
                'start': {'x': 0.16, 'y': 0.12},
                'end': {'x': 0.16, 'y': 0.55},
              },
              durationMs: 400,
              delayMs: 100,
              strokeWidth: 2.0,
            ),
            // Step 3: Bob (circle at bottom)
            TemplateStep(
              id: 'bob',
              action: TemplateDrawAction.drawShape,
              geometry: {
                'shape': 'circle',
                'position': {'x': 0.15, 'y': 0.58},
                'size': {'w': 0.06, 'h': 0.06},
                'fillColor': 'primary',
              },
              durationMs: 400,
              delayMs: 100,
              strokeWidth: 2.0,
            ),
            // Step 4: Swinging arc (dotted quarter circle)
            TemplateStep(
              id: 'arc',
              action: TemplateDrawAction.drawPath,
              geometry: {
                'points': [
                  {'x': 0.16, 'y': 0.55},
                  {'x': 0.10, 'y': 0.50},
                  {'x': 0.22, 'y': 0.50},
                ],
                'closed': false,
                'smooth': true,
              },
              durationMs: 500,
              delayMs: 150,
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
            'pivot_label': TemplateLabel(
              id: 'pivot_label',
              text: 'Pivot',
              position: const BoardPoint(0.18, 0.05),
              style: 'label',
            ),
            'rod_label': TemplateLabel(
              id: 'rod_label',
              text: 'Rod',
              position: const BoardPoint(0.14, 0.35),
              style: 'caption',
            ),
            'bob_label': TemplateLabel(
              id: 'bob_label',
              text: 'Bob',
              position: const BoardPoint(0.18, 0.65),
              style: 'caption',
            ),
          },
          animationMetadata: const {
            'easing': 'easeOutCubic',
            'overallDurationMs': 2100,
          },
        );
}
