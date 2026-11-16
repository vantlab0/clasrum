import 'dart:ui' show Color;

import '../../model/board_geometry.dart';
import '../template_definition.dart';

/// Simple electric circuit template (physics).
///
/// Draws a battery, resistor, capacitor, and wires in a loop.
/// Progressive drawing shows the circuit being built from the power source.
class CircuitTemplate extends Template {
  CircuitTemplate() : super(
    id: 'electric_circuit',
    category: TemplateCategory.physics,
    displayName: 'Electric Circuit',
    defaultSize: const BoardSize(0.32, 0.18),
    steps: const [
      // Step 1: Battery
      TemplateStep(
        id: 'battery',
        action: TemplateDrawAction.drawPath,
        geometry: {
          'points': [
            {'x': 0.05, 'y': 0.12},
            {'x': 0.05, 'y': 0.26},
            {'x': 0.08, 'y': 0.26},
            {'x': 0.08, 'y': 0.12},
          ],
          'closed': true,
          'smooth': false,
        },
        durationMs: 500,
        strokeWidth: 2.5,
        labelId: 'battery_label',
      ),
      // Step 2: Top wire
      TemplateStep(
        id: 'top_wire',
        action: TemplateDrawAction.drawPath,
        geometry: {
          'points': [
            {'x': 0.08, 'y': 0.12},
            {'x': 0.28, 'y': 0.12},
          ],
          'closed': false,
          'smooth': false,
        },
        durationMs: 400,
        delayMs: 100,
        strokeWidth: 2.0,
      ),
      // Step 3: Resistor
      TemplateStep(
        id: 'resistor',
        action: TemplateDrawAction.drawPath,
        geometry: {
          'points': [
            {'x': 0.12, 'y': 0.12},
            {'x': 0.14, 'y': 0.10},
            {'x': 0.16, 'y': 0.14},
            {'x': 0.18, 'y': 0.10},
            {'x': 0.20, 'y': 0.14},
            {'x': 0.22, 'y': 0.10},
            {'x': 0.24, 'y': 0.12},
          ],
          'closed': false,
          'smooth': false,
        },
        durationMs: 600,
        delayMs: 150,
        labelId: 'resistor_label',
        strokeWidth: 2.0,
      ),
      // Step 4: Bottom wire
      TemplateStep(
        id: 'bottom_wire',
        action: TemplateDrawAction.drawPath,
        geometry: {
          'points': [
            {'x': 0.24, 'y': 0.12},
            {'x': 0.05, 'y': 0.12},
          ],
          'closed': false,
          'smooth': false,
        },
        durationMs: 400,
        delayMs: 200,
        strokeWidth: 2.0,
      ),
    ],
    labels: {
      'battery_label': TemplateLabel(
        id: 'battery_label',
        text: 'Battery',
        position: const BoardPoint(0.02, 0.30),
        targetId: 'battery',
        style: 'label',
      ),
      'resistor_label': TemplateLabel(
        id: 'resistor_label',
        text: 'Resistor',
        position: const BoardPoint(0.18, 0.04),
        targetId: 'resistor',
        style: 'label',
      ),
    },
    animationMetadata: const {
      'easing': 'easeOutCubic',
      'overallDurationMs': 1900,
    },
  );
}
