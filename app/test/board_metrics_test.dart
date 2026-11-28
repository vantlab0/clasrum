import 'package:flutter_test/flutter_test.dart';
import 'package:pwony/blackboard/blackboard.dart';

BoardEvent _equation({
  required int sequence,
  required String latex,
  double? maxWidth,
}) {
  return BoardEvent.fromJson(<String, dynamic>{
    'event_id': 'metrics_$sequence',
    'session_id': 'sess_metrics',
    'sequence': sequence,
    'type': 'write_equation',
    'element_id': 'eq_$sequence',
    'payload': <String, dynamic>{
      'latex': latex,
      'style': 'equation',
      'position': <String, dynamic>{'x': 0.1, 'y': 0.1},
      if (maxWidth != null) 'max_width': maxWidth,
    },
    'created_at': '2026-11-28T00:00:00.000Z',
    'board_revision': sequence,
    'animate': false,
  });
}

void main() {
  test('long equations get wrapped bounds instead of a single-line box', () {
    final latex = r'\int_0^1 \frac{x^2 + y^2}{\sqrt{1 - x^2}} \, dx + '
        r'\sum_{i=0}^{n} \alpha_i \beta_i \gamma_i \delta_i';
    final doc = BoardDocument.empty(sessionId: 'sess_metrics').applyAll(<BoardEvent>[
      _equation(sequence: 1, latex: latex, maxWidth: 0.4),
    ]);
    final bounds = doc.boundsOf(<String>{'eq_1'});
    expect(bounds, isNotNull);
    expect(bounds!.w, lessThanOrEqualTo(0.4 + 0.0001));
    // Multiple wrapping rows are taller than a one-line equation box.
    expect(bounds.h, greaterThan(0.09));
    expect(bounds.bottom, lessThanOrEqualTo(1.0));
  });

  test('declared equation size wins over text-length estimate', () {
    final doc = BoardDocument.empty(sessionId: 'sess_metrics').applyAll(<BoardEvent>[
      BoardEvent.fromJson(<String, dynamic>{
        'event_id': 'metrics_sized',
        'session_id': 'sess_metrics',
        'sequence': 2,
        'type': 'write_equation',
        'element_id': 'eq_sized',
        'payload': <String, dynamic>{
          'latex': r'E = mc^2',
          'style': 'equation',
          'position': <String, dynamic>{'x': 0.2, 'y': 0.2},
          'size': <String, dynamic>{'w': 0.25, 'h': 0.12},
        },
        'created_at': '2026-11-28T00:00:00.000Z',
        'board_revision': 2,
        'animate': false,
      }),
    ]);
    final bounds = doc.boundsOf(<String>{'eq_sized'});
    expect(bounds, isNotNull);
    expect(bounds!.w, closeTo(0.25, 0.0001));
    expect(bounds.h, closeTo(0.12, 0.0001));
  });
}
