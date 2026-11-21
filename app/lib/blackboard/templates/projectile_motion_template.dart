import 'dart:ui' show Color;

import '../model/board_geometry.dart';
import 'template_definition.dart';

/// Projectile motion template (physics).
///
/// Draws a projectile trajectory with launch angle and landing point.
/// Progressive drawing reveals from launch point through the trajectory to landing.
class ProjectileMotionTemplate extends Template {
  ProjectileMotionTemplate()
      : super(
          id: 'projectile_motion',
          category: TemplateCategory.physics,
          displayName: 'Projectile Motion',
          defaultSize: const BoardSize(0.32, 0.22),
          steps: const [
            // Step 1: Launch point
            TemplateStep(
              id: 'launch_point',
              action: TemplateDrawAction.drawShape,
              geometry: {
                'shape': 'circle',
                'position': {'x': 0.15, 'y': 0.70},
                'size': {'w': 0.03, 'h': 0.03},
                'fillColor': 'primary',
              },
              durationMs: 300,
              strokeWidth: 2.0,
            ),
            // Step 2: Launch angle line
            TemplateStep(
              id: 'launch_angle',
              action: TemplateDrawAction.drawLine,
              geometry: {
                'start': {'x': 0.15, 'y': 0.70},
                'end': {'x': 0.25, 'y': 0.55},
              },
              durationMs: 400,
              delayMs: 100,
              strokeWidth: 2.0,
            ),
            // Step 3: Trajectory parabola
            TemplateStep(
              id: 'trajectory',
              action: TemplateDrawAction.drawPath,
              geometry: {
                'points': [
                  {'x': 0.15, 'y': 0.70},
                  {'x': 0.25, 'y': 0.55},
                  {'x': 0.40, 'y': 0.40},
                  {'x': 0.55, 'y': 0.25},
                  {'x': 0.70, 'y': 0.10},
                  {'x': 0.80, 'y': 0.05},
                ],
                'closed': false,
                'smooth': true,
              },
              durationMs: 600,
              delayMs: 150,
              strokeWidth: 2.0,
            ),
            // Step 4: Landing point
            TemplateStep(
              id: 'landing_point',
              action: TemplateDrawAction.drawShape,
              geometry: {
                'shape': 'circle',
                'position': {'x': 0.80, 'y': 0.70},
                'size': {'w': 0.03, 'h': 0.03},
                'fillColor': 'primary',
              },
              durationMs: 300,
              delayMs: 100,
              strokeWidth: 2.0,
            ),
            // Step 5: Maximum height indicator
            TemplateStep(
              id: 'max_height',
              action: TemplateDrawAction.drawLine,
              geometry: {
                'start': {'x': 0.40, 'y': 0.40},
                'end': {'x': 0.40, 'y': 0.25},
              },
              durationMs: 300,
              delayMs: 100,
              strokeWidth: 1.5,
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
            'launch_label': TemplateLabel(
              id: 'launch_label',
              text: 'Launch',
              position: const BoardPoint(0.12, 0.75),
              style: 'label',
            ),
            'angle_label': TemplateLabel(
              id: 'angle_label',
              text: 'θ',
              position: const BoardPoint(0.20, 0.65),
              style: 'caption',
            ),
            'height_label': TemplateLabel(
              id: 'height_label',
              text: 'hₘₐₓ',
              position: const BoardPoint(0.38, 0.33),
              style: 'caption',
            ),
            'range_label': TemplateLabel(
              id: 'range_label',
              text: 'Range',
              position: const BoardPoint(0.78, 0.75),
              style: 'caption',
            ),
            'landing_label': TemplateLabel(
              id: 'landing_label',
              text: 'Landing',
              position: const BoardPoint(0.83, 0.70),
              style: 'caption',
            ),
          },
          animationMetadata: const {
            'easing': 'easeOutCubic',
            'overallDurationMs': 2700,
          },
        );
}
