import 'package:ai_classroom/core/formatters.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('clockDuration formats short and long durations', () {
    expect(clockDuration(-5), '00:00');
    expect(clockDuration(65), '01:05');
    expect(clockDuration(3661), '1:01:01');
  });

  test('percentLabel accepts fractions and legacy percentages', () {
    expect(percentLabel(0.42), '42%');
    expect(percentLabel(42), '42%');
    expect(percentLabel(150), '100%');
  });
}
