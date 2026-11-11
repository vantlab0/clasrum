import 'dart:math' as math;
import 'dart:ui' show Offset, Rect;

import 'board_geometry.dart';
import 'board_transform.dart';

/// Inclusive numeric range used by graph coordinate systems.
class BoardRange {
  const BoardRange(this.min, this.max);

  factory BoardRange.normalized(double min, double max) =>
      min <= max ? BoardRange(min, max) : BoardRange(max, min);

  /// Parses `[min, max]` from a payload, falling back to a sane default range.
  static BoardRange? tryParse(Object? json) {
    if (json is BoardRange) return json;
    if (json is! List || json.length < 2) return null;
    final min = _num(json[0]);
    final max = _num(json[1]);
    if (min == null || max == null || !min.isFinite || !max.isFinite) {
      return null;
    }
    if (min == max) return BoardRange(min - 1, max + 1);
    return BoardRange.normalized(min, max);
  }

  final double min;
  final double max;

  bool get isValid => min.isFinite && max.isFinite && max > min;

  double get span => max - min;

  bool contains(double value) => value >= min && value <= max;

  double clampValue(double value) => value.clamp(min, max);

  double normalize(double value) => (value - min) / span;

  @override
  bool operator ==(Object other) =>
      other is BoardRange && other.min == min && other.max == max;

  @override
  int get hashCode => Object.hash(min, max);

  @override
  String toString() => 'BoardRange($min, $max)';
}

/// Rounds a raw axis step up to a "nice" 1/2/2.5/5 × 10^k value.
double niceStep(double rawStep) {
  if (!rawStep.isFinite || rawStep <= 0) return 1;
  final exponent = (math.log(rawStep) / math.ln10).floor();
  final magnitude = math.pow(10, exponent).toDouble();
  final fraction = rawStep / magnitude;
  double niceFraction;
  if (fraction <= 1) {
    niceFraction = 1;
  } else if (fraction <= 2) {
    niceFraction = 2;
  } else if (fraction <= 2.5) {
    niceFraction = 2.5;
  } else if (fraction <= 5) {
    niceFraction = 5;
  } else {
    niceFraction = 10;
  }
  return niceFraction * magnitude;
}

/// A set of uniformly spaced axis ticks plus display labels.
class TickSet {
  const TickSet({required this.values, required this.labels, required this.step});

  final List<double> values;
  final List<String> labels;
  final double step;

  static const TickSet empty = TickSet(
    values: <double>[],
    labels: <String>[],
    step: 1,
  );

  bool get isEmpty => values.isEmpty;
}

/// Computes ticks covering [range] with a nice step (or [preferredStep]).
TickSet computeTicks(
  BoardRange range, {
  int targetCount = 6,
  double? preferredStep,
}) {
  if (!range.isValid) return TickSet.empty;
  final rawStep =
      preferredStep != null && preferredStep > 0
          ? preferredStep
          : range.span / math.max(1, targetCount - 1);
  final step = preferredStep != null && preferredStep > 0
      ? preferredStep
      : niceStep(rawStep);
  if (step <= 0 || !step.isFinite) return TickSet.empty;
  final start = (range.min / step).ceil() * step;
  final values = <double>[];
  final labels = <String>[];
  const epsilon = 1e-9;
  var value = start;
  for (var i = 0; i < 64 && value <= range.max + step * epsilon; i++) {
    final rounded = _roundToStep(value, step);
    if (rounded >= range.min - step * epsilon &&
        rounded <= range.max + step * epsilon) {
      values.add(rounded);
      labels.add(formatTickValue(rounded, step));
    }
    value += step;
  }
  return TickSet(
    values: List<double>.unmodifiable(values),
    labels: List<String>.unmodifiable(labels),
    step: step,
  );
}

/// Formats a tick value for an axis label, hiding float noise.
String formatTickValue(double value, double step) {
  if (!value.isFinite) return '';
  final magnitude = value.abs();
  if (magnitude >= 1e6 || (magnitude > 0 && magnitude < 1e-4)) {
    return _stripTrailingZeros(value.toStringAsExponential(2));
  }
  var decimals = 0;
  if (step > 0 && step < 1) {
    decimals = (-math.log(step) / math.ln10).floor() + 1;
    if (decimals < 1) decimals = 1;
  }
  if (decimals > 6) decimals = 6;
  var text = value.toStringAsFixed(decimals);
  if (text.contains('.')) text = _stripTrailingZeros(text);
  if (text == '-0') text = '0';
  return text;
}

String _stripTrailingZeros(String text) {
  if (!text.contains('.')) return text;
  var result = text.replaceAll(RegExp(r'0+$'), '');
  if (result.endsWith('.')) result = result.substring(0, result.length - 1);
  return result.isEmpty ? '0' : result;
}

double _roundToStep(double value, double step) {
  final decimals = step >= 1 ? 0 : (-math.log(step) / math.ln10).ceil() + 2;
  final factor = math.pow(10, decimals.clamp(0, 8)).toDouble();
  return (value * factor).round() / factor;
}

/// Linear data-space → screen-space (or normalized) mapping.
class LinearMap {
  const LinearMap({
    required this.inputMin,
    required this.inputMax,
    required this.outputMin,
    required this.outputMax,
  });

  final double inputMin;
  final double inputMax;
  final double outputMin;
  final double outputMax;

  double get inputSpan => inputMax - inputMin;
  double get outputSpan => outputMax - outputMin;

  double map(double value) {
    if (inputSpan == 0 || !inputSpan.isFinite) return outputMin;
    return outputMin + (value - inputMin) / inputSpan * outputSpan;
  }

  double invert(double value) {
    if (outputSpan == 0 || !outputSpan.isFinite) return inputMin;
    return inputMin + (value - outputMin) / outputSpan * inputSpan;
  }
}

/// Maps a graph's data coordinates into normalized board coordinates inside the
/// coordinate system rect, then (via [BoardTransform]) into pixels. Handles the
/// inverted y axis (math y up, board y down).
class GraphMapper {
  const GraphMapper({
    required this.xRange,
    required this.yRange,
    required this.rect,
  });

  final BoardRange xRange;
  final BoardRange yRange;
  final BoardRect rect;

  LinearMap get xMap =>
      LinearMap(inputMin: xRange.min, inputMax: xRange.max, outputMin: rect.x, outputMax: rect.right);

  LinearMap get yMap =>
      LinearMap(inputMin: yRange.min, inputMax: yRange.max, outputMin: rect.bottom, outputMax: rect.y);

  BoardPoint dataToNormalized(double x, double y) =>
      BoardPoint(xMap.map(x), yMap.map(y));

  Offset dataToPixels(double x, double y, BoardTransform transform) =>
      transform.toPixels(dataToNormalized(x, y));

  /// Pixel position of a data point, or null when the mapper has degenerated.
  Offset? tryDataToPixels(double x, double y, BoardTransform transform) {
    if (!x.isFinite || !y.isFinite || !xRange.isValid || !yRange.isValid) {
      return null;
    }
    return dataToPixels(x, y, transform);
  }

  Offset pixelsToData(Offset pixels, BoardTransform transform) {
    final normalized = transform.toNormalized(pixels);
    return Offset(xMap.invert(normalized.x), yMap.invert(normalized.y));
  }

  bool containsData(double x, double y) => xRange.contains(x) && yRange.contains(y);

  Rect pixelsRect(BoardTransform transform) => transform.rectToPixels(rect);

  TickSet get xTicks => computeTicks(xRange);
  TickSet get yTicks => computeTicks(yRange);
}

/// Pixel-space axis segments and tick labels for one coordinate system.
class AxisGeometry {
  const AxisGeometry({
    required this.plotRect,
    required this.xAxisY,
    required this.yAxisX,
    required this.xAxisAtEdge,
    required this.yAxisAtEdge,
    required this.xTicks,
    required this.yTicks,
    required this.originPixel,
    required this.originInside,
  });

  final Rect plotRect;
  final double xAxisY;
  final double yAxisX;
  final bool xAxisAtEdge;
  final bool yAxisAtEdge;
  final List<AxisTick> xTicks;
  final List<AxisTick> yTicks;
  final Offset originPixel;
  final bool originInside;
}

/// One formatted tick: its value, label, pixel position on the axis, and the
/// pixel anchor for the label text (top-left unless documented otherwise).
class AxisTick {
  const AxisTick({
    required this.value,
    required this.label,
    required this.pixel,
    required this.labelPixel,
    required this.labelAnchor,
  });

  final double value;
  final String label;
  final Offset pixel;
  final Offset labelPixel;

  /// Alignment of the label relative to [labelPixel]:
  /// `centerTop`, `rightCenter`, `centerBottom`, `leftCenter`.
  final String labelAnchor;
}

/// Computes axes, ticks and non-overlapping label layout for a coordinate
/// system. [measureTextWidth] and [labelHeight] let the caller use the real
/// chalk font metrics without pulling TextPainter into this pure module.
AxisGeometry layoutAxisGeometry({
  required GraphMapper mapper,
  required BoardTransform transform,
  required TickSet xTicks,
  required TickSet yTicks,
  required double labelHeight,
  required double Function(String label) measureTextWidth,
  double labelPadding = 4,
}) {
  final plotRect = mapper.pixelsRect(transform);
  final xAxisAtZero = mapper.yRange.contains(0);
  final yAxisAtZero = mapper.xRange.contains(0);
  final xAxisY = xAxisAtZero
      ? mapper.dataToPixels(0, 0, transform).dy
      : (mapper.yRange.min > 0 ? plotRect.bottom : plotRect.top);
  final yAxisX = yAxisAtZero
      ? mapper.dataToPixels(0, 0, transform).dx
      : (mapper.xRange.min > 0 ? plotRect.left : plotRect.right);
  final xAxisAtEdge = xAxisY <= plotRect.top + 0.5 || xAxisY >= plotRect.bottom - 0.5;
  final yAxisAtEdge = yAxisX <= plotRect.left + 0.5 || yAxisX >= plotRect.right - 0.5;

  final xLabels = <AxisTick>[];
  final xOccupied = <Rect>[];
  final xAnchorTop = xAxisAtZero ? xAxisY + labelPadding : xAxisY + labelPadding;
  for (var i = 0; i < xTicks.values.length; i++) {
    final value = xTicks.values[i];
    final label = xTicks.labels[i];
    final pixel = Offset(mapper.dataToPixels(value, 0, transform).dx, xAxisY);
    final width = measureTextWidth(label);
    final candidates = <Offset>[
      Offset(pixel.dx - width / 2, xAnchorTop),
      Offset(pixel.dx - width / 2, xAxisY - labelHeight - labelPadding),
    ];
    Offset? chosen;
    for (final candidate in candidates) {
      final rect = Rect.fromLTWH(candidate.dx, candidate.dy, width, labelHeight);
      if (!_overlapsAny(rect, xOccupied) &&
          rect.left >= plotRect.left - 2 &&
          rect.right <= plotRect.right + 2 &&
          rect.top >= 0 &&
          rect.bottom <= transform.size.height) {
        chosen = candidate;
        break;
      }
    }
    if (chosen == null) continue;
    final rect =
        Rect.fromLTWH(chosen.dx, chosen.dy, width, labelHeight);
    xOccupied.add(rect);
    xLabels.add(
      AxisTick(
        value: value,
        label: label,
        pixel: pixel,
        labelPixel: chosen,
        labelAnchor: 'topLeft',
      ),
    );
  }

  final yLabels = <AxisTick>[];
  final yOccupied = <Rect>[];
  final labelRightEdge = yAxisAtZero
      ? yAxisX - labelPadding
      : plotRect.left - labelPadding;
  for (var i = 0; i < yTicks.values.length; i++) {
    final value = yTicks.values[i];
    final label = yTicks.labels[i];
    final pixel = Offset(yAxisX, mapper.dataToPixels(0, value, transform).dy);
    final width = measureTextWidth(label);
    final candidates = <Offset>[
      Offset(labelRightEdge - width, pixel.dy - labelHeight / 2),
      Offset(yAxisX + labelPadding, pixel.dy - labelHeight / 2),
    ];
    Offset? chosen;
    for (final candidate in candidates) {
      final rect = Rect.fromLTWH(candidate.dx, candidate.dy, width, labelHeight);
      if (!_overlapsAny(rect, yOccupied) &&
          !_overlapsAny(rect, xOccupied) &&
          rect.left >= plotRect.left - 2 &&
          rect.right <= plotRect.right + 2 &&
          rect.top >= 0 &&
          rect.bottom <= transform.size.height) {
        chosen = candidate;
        break;
      }
    }
    if (chosen == null) continue;
    yOccupied.add(Rect.fromLTWH(chosen.dx, chosen.dy, width, labelHeight));
    yLabels.add(
      AxisTick(
        value: value,
        label: label,
        pixel: pixel,
        labelPixel: chosen,
        labelAnchor: 'topLeft',
      ),
    );
  }

  final originPixel = mapper.dataToPixels(0, 0, transform);
  return AxisGeometry(
    plotRect: plotRect,
    xAxisY: xAxisY,
    yAxisX: yAxisX,
    xAxisAtEdge: xAxisAtEdge,
    yAxisAtEdge: yAxisAtEdge,
    xTicks: List<AxisTick>.unmodifiable(xLabels),
    yTicks: List<AxisTick>.unmodifiable(yLabels),
    originPixel: originPixel,
    originInside: xAxisAtZero && yAxisAtZero,
  );
}

bool _overlapsAny(Rect rect, List<Rect> others) {
  for (final other in others) {
    if (rect.overlaps(other)) return true;
  }
  return false;
}

/// Sampling consumer for `plot_function`: sanitizes the backend-provided
/// samples and produces the partially-revealed polyline points for a progress
/// value in `[0, 1]` (progressive left→right unveil).
class SeriesSampler {
  const SeriesSampler._();

  /// Sorts by x, drops non-finite values and duplicate x positions.
  static List<BoardPoint> sanitize(Iterable<BoardPoint> samples) {
    final list = <BoardPoint>[];
    for (final sample in samples) {
      if (!sample.isFinite) continue;
      list.add(sample);
    }
    if (list.isEmpty) return const <BoardPoint>[];
    list.sort((a, b) => a.x.compareTo(b.x));
    final result = <BoardPoint>[list.first];
    for (var i = 1; i < list.length; i++) {
      final previous = result.last;
      if ((list[i].x - previous.x).abs() < 1e-12) {
        // Keep the latest y for a duplicated x, which matches function plotting.
        result[result.length - 1] = list[i];
      } else {
        result.add(list[i]);
      }
    }
    return List<BoardPoint>.unmodifiable(result);
  }

  /// Number of samples fully or partially visible at [progress].
  static int revealedCount(int sampleCount, double progress) {
    if (sampleCount <= 0 || progress <= 0) return 0;
    if (progress >= 1) return sampleCount;
    return (progress * (sampleCount - 1)).floor() + 1;
  }

  /// Partial polyline including an interpolated head point so the reveal looks
  /// like chalk being drawn continuously, not sample-by-sample popping.
  static List<BoardPoint> revealed(List<BoardPoint> samples, double progress) {
    if (samples.isEmpty || progress <= 0) return const <BoardPoint>[];
    if (progress >= 1 || samples.length == 1) {
      return List<BoardPoint>.unmodifiable(samples);
    }
    final position = progress * (samples.length - 1);
    final index = position.floor();
    final fraction = position - index;
    final head = index >= samples.length - 1
        ? samples.last
        : BoardPoint(
            samples[index].x +
                (samples[index + 1].x - samples[index].x) * fraction,
            samples[index].y +
                (samples[index + 1].y - samples[index].y) * fraction,
          );
    return List<BoardPoint>.unmodifiable(<BoardPoint>[
      ...samples.take(index + 1),
      head,
    ]);
  }
}

/// Point-plotting math for `plot_points` (data → pixels, marker radius, label
/// anchor, leader lines for graph labels).
class PlotMath {
  const PlotMath._();

  static Offset? markerPixel(
    GraphMapper mapper,
    BoardTransform transform,
    double x,
    double y,
  ) =>
      mapper.tryDataToPixels(x, y, transform);

  /// Marker radius in pixels: payload radius is normalized (fraction of the
  /// board unit) with a readable pixel floor.
  static double markerRadius(BoardTransform transform, double? normalizedRadius) {
    if (normalizedRadius == null || !normalizedRadius.isFinite) {
      return math.max(2.5, transform.unit * 0.007);
    }
    return (normalizedRadius * transform.unit).clamp(2.0, 18.0);
  }

  /// Where a point label starts, just up-right of the marker.
  static Offset labelAnchor(Offset marker, double radius, double fontSize) =>
      Offset(marker.dx + radius + 3, marker.dy - radius - fontSize * 0.9);

  /// Leader line for `add_graph_label`: from the data point to the text anchor.
  static (Offset start, Offset end) leader(
    Offset dataPixel,
    Offset textPixel,
    double fontSize,
  ) {
    final end = Offset(textPixel.dx, textPixel.dy + fontSize * 0.5);
    return (dataPixel, end);
  }
}

double? _num(Object? value) {
  if (value is num) return value.toDouble();
  if (value is String) return double.tryParse(value.trim());
  return null;
}
