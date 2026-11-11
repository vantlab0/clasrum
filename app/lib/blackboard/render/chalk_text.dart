import 'dart:math' as math;
import 'dart:ui' as ui;

import 'package:flutter/painting.dart';

import '../model/board_geometry.dart';
import '../model/board_metrics.dart';

/// Bundled chalk fonts (see `app/assets/fonts/README.md`). The renderer always
/// falls back to the platform font when the asset/family is unavailable, so
/// the board still renders in tests or if fonts fail to load.
class ChalkFonts {
  const ChalkFonts._();

  /// Family declared in pubspec.yaml for `PatrickHand-Regular.ttf`.
  static const String script = 'Chalkboard';

  /// Secondary family declared for the optional `Caveat` accent face.
  static const String accent = 'ChalkScript';

  static const List<String> fallback = <String>[
    'ChalkScript',
    'Segoe Print',
    'Comic Sans MS',
  ];

  /// Code-block presentation family (CONTRACTS §3.1 `code` style).
  static const List<String> monospaceFallback = <String>[
    'Courier New',
    'Consolas',
    'monospace',
  ];
}

/// Per-style chalk typography: normalized size + weight/letter spacing/opacity.
class ChalkTextStyleSpec {
  const ChalkTextStyleSpec({
    required this.name,
    required this.heightFraction,
    this.weight = FontWeight.w400,
    this.letterSpacingEm = 0.01,
    this.lineHeight = BoardTextMetrics.lineHeight,
    this.opacity = 1,
    this.monospace = false,
  });

  final String name;

  /// Font size as a fraction of the board content height.
  final double heightFraction;
  final FontWeight weight;
  final double letterSpacingEm;
  final double lineHeight;
  final double opacity;
  final bool monospace;

  static const Map<String, ChalkTextStyleSpec> _specs =
      <String, ChalkTextStyleSpec>{
    'heading': ChalkTextStyleSpec(
      name: 'heading',
      heightFraction: 0.056,
      weight: FontWeight.w600,
      letterSpacingEm: 0.02,
    ),
    'subheading': ChalkTextStyleSpec(
      name: 'subheading',
      heightFraction: 0.043,
      weight: FontWeight.w500,
      letterSpacingEm: 0.015,
    ),
    'body': ChalkTextStyleSpec(name: 'body', heightFraction: 0.032),
    'definition': ChalkTextStyleSpec(
      name: 'definition',
      heightFraction: 0.032,
      opacity: 0.98,
    ),
    'definitionTerm': ChalkTextStyleSpec(
      name: 'definitionTerm',
      heightFraction: 0.038,
      weight: FontWeight.w500,
      letterSpacingEm: 0.015,
    ),
    'bullet': ChalkTextStyleSpec(name: 'bullet', heightFraction: 0.031),
    'numbered': ChalkTextStyleSpec(name: 'numbered', heightFraction: 0.031),
    'label': ChalkTextStyleSpec(name: 'label', heightFraction: 0.024),
    'caption': ChalkTextStyleSpec(
      name: 'caption',
      heightFraction: 0.021,
      opacity: 0.82,
    ),
    'note': ChalkTextStyleSpec(
      name: 'note',
      heightFraction: 0.027,
      opacity: 0.9,
    ),
    'code': ChalkTextStyleSpec(
      name: 'code',
      heightFraction: 0.026,
      monospace: true,
      letterSpacingEm: 0,
      lineHeight: 1.35,
    ),
  };

  /// Style spec for a CONTRACTS §3.1 style name (`heading`, `body`, `code`…).
  static ChalkTextStyleSpec forName(
    String? style, {
    int headingLevel = 1,
    bool definitionTerm = false,
  }) {
    if (definitionTerm) return _specs['definitionTerm']!;
    final normalized = (style ?? 'body').toLowerCase();
    if (normalized == 'heading') {
      return headingLevel <= 1
          ? _specs['heading']!
          : _specs['subheading']!;
    }
    if (normalized == 'numbered') return _specs['numbered']!;
    return _specs[normalized] ?? _specs['body']!;
  }

  String get cacheKey =>
      '$name|${heightFraction.toStringAsFixed(4)}|${weight.value}|'
      '${letterSpacingEm.toStringAsFixed(3)}|${lineHeight.toStringAsFixed(2)}|'
      '${opacity.toStringAsFixed(2)}|$monospace';
}

/// One chalk-dust speck generated deterministically for a text layout.
class ChalkSpeck {
  const ChalkSpeck(this.dx, this.dy, this.radius, this.alpha);

  final double dx;
  final double dy;
  final double radius;
  final double alpha;
}

/// A laid-out run of chalk text plus its deterministic dust pattern.
class ChalkTextLayout {
  ChalkTextLayout({
    required this.text,
    required this.painter,
    required this.spec,
    required this.color,
    required this.fontSizePx,
    required this.maxWidthPx,
    required this.grain,
  });

  final String text;
  final TextPainter painter;
  final ChalkTextStyleSpec spec;
  final Color color;
  final double fontSizePx;
  final double maxWidthPx;
  final List<ChalkSpeck> grain;

  double get width => painter.width;
  double get height => painter.height;

  void dispose() => painter.dispose();
}

/// Measures and paints chalk text with a progressive left→right stroke reveal.
///
/// TextPainters are cached by content/style/color/size/max-width (measurement
/// cache, CONTRACTS §10 render requirements); the progressive reveal clips the
/// already-laid-out glyph run along the writing direction, adds a deterministic
/// dust band at the reveal edge and a soft chalk halo, so it reads as chalk
/// being drawn — never as a whole-widget fade.
class ChalkTextEngine {
  ChalkTextEngine({this.maxCachedLayouts = 128});

  final int maxCachedLayouts;
  final Map<String, ChalkTextLayout> _cache = <String, ChalkTextLayout>{};
  final List<String> _lru = <String>[];

  int get cachedLayoutCount => _cache.length;

  /// Lay out [text]. [maxWidthPx] must be finite and > 0 (pass a large value
  /// when the text should not wrap).
  ChalkTextLayout layout({
    required String text,
    required ChalkTextStyleSpec spec,
    required Color color,
    required double fontSizePx,
    required double maxWidthPx,
    TextAlign textAlign = TextAlign.left,
  }) {
    final safeSize = fontSizePx.clamp(6.0, 200.0);
    final safeWidth = maxWidthPx.isFinite && maxWidthPx > 0
        ? maxWidthPx
        : 10000.0;
    final key =
        '${_stableHash(text)}|${spec.cacheKey}|${color.toARGB32()}|'
        '${safeSize.toStringAsFixed(2)}|${safeWidth.round()}|${textAlign.index}';
    final cached = _cache[key];
    if (cached != null) {
      _touch(key);
      return cached;
    }
    final painter = TextPainter(
      text: TextSpan(
        text: text,
        style: _textStyle(
          spec: spec,
          color: color,
          fontSizePx: safeSize,
        ),
      ),
      textDirection: TextDirection.ltr,
      textAlign: textAlign,
      maxLines: null,
    )..layout(maxWidth: safeWidth);
    final layout = ChalkTextLayout(
      text: text,
      painter: painter,
      spec: spec,
      color: color,
      fontSizePx: safeSize,
      maxWidthPx: safeWidth,
      grain: _generateGrain(text, spec, painter.width, painter.height, safeSize),
    );
    _cache[key] = layout;
    _lru.add(key);
    while (_cache.length > maxCachedLayouts) {
      final oldest = _lru.removeAt(0);
      _cache.remove(oldest)?.dispose();
    }
    return layout;
  }

  /// Convenience measure without retaining the result in the caller.
  Size measure({
    required String text,
    required ChalkTextStyleSpec spec,
    required Color color,
    required double fontSizePx,
    required double maxWidthPx,
    TextAlign textAlign = TextAlign.left,
  }) {
    final layout = this.layout(
      text: text,
      spec: spec,
      color: color,
      fontSizePx: fontSizePx,
      maxWidthPx: maxWidthPx,
      textAlign: textAlign,
    );
    return Size(layout.width, layout.height);
  }

  /// Paints [text] with its reveal [progress] (0 = nothing, 1 = complete).
  ///
  /// [origin] is the top-left pixel anchor. Returns the painted pixel bounds.
  Rect paintText(
    ui.Canvas canvas,
    String text, {
    required Offset origin,
    required ChalkTextStyleSpec spec,
    required Color color,
    required double fontSizePx,
    required double maxWidthPx,
    double progress = 1,
    double opacity = 1,
    TextAlign textAlign = TextAlign.left,
    bool underline = false,
  }) {
    final layout = this.layout(
      text: text,
      spec: spec,
      color: color,
      fontSizePx: fontSizePx,
      maxWidthPx: maxWidthPx,
      textAlign: textAlign,
    );
    paintLayout(
      canvas,
      layout,
      origin: origin,
      progress: progress,
      opacity: opacity,
      underline: underline,
    );
    return Rect.fromLTWH(origin.dx, origin.dy, layout.width, layout.height);
  }

  /// Paints an already-laid-out run (avoids re-measuring inside loops).
  void paintLayout(
    ui.Canvas canvas,
    ChalkTextLayout layout, {
    required Offset origin,
    double progress = 1,
    double opacity = 1,
    bool underline = false,
  }) {
    final clamped = progress.clamp(0.0, 1.0);
    if (clamped <= 0.002 || opacity <= 0.002) return;
    final scale = (layout.fontSizePx / 32).clamp(0.5, 3.0);
    canvas.save();
    canvas.translate(origin.dx, origin.dy);
    final revealWidth =
        layout.width * clamped + layout.fontSizePx * 0.35 + 1;
    final clipHeight = layout.height + layout.fontSizePx;
    canvas.clipRect(
      Rect.fromLTWH(
        -layout.fontSizePx * 0.25,
        -layout.fontSizePx * 0.45,
        revealWidth,
        clipHeight,
      ),
    );

    final alpha = opacity * layout.spec.opacity;
    if (clamped < 1) {
      // Soft chalk halo only for the actively-drawn run (expensive layer blur).
      final haloPaint = Paint()
        ..color = Color.fromRGBO(0, 0, 0, 0.32 * alpha)
        ..imageFilter = ui.ImageFilter.blur(
          sigmaX: 1.15 * scale,
          sigmaY: 1.15 * scale,
          tileMode: TileMode.decal,
        );
      final bounds = Rect.fromLTWH(
        -layout.fontSizePx * 0.3,
        -layout.fontSizePx * 0.3,
        layout.width + layout.fontSizePx,
        layout.height + layout.fontSizePx,
      );
      canvas.saveLayer(bounds, haloPaint);
      layout.painter.paint(canvas, Offset.zero);
      canvas.restore();
    }

    canvas.saveLayer(
      Rect.fromLTWH(0, 0, layout.width + 2, layout.height + 2),
      Paint()..color = Color.fromRGBO(0, 0, 0, alpha),
    );
    layout.painter.paint(canvas, Offset.zero);
    canvas.restore();

    _paintGrain(canvas, layout, clamped, alpha);

    if (underline) {
      _paintUnderline(canvas, layout, clamped, alpha);
    }
    canvas.restore();
  }

  void _paintGrain(
    ui.Canvas canvas,
    ChalkTextLayout layout,
    double progress,
    double alpha,
  ) {
    final revealX = layout.width * progress;
    final paint = Paint()..style = PaintingStyle.fill;
    for (final speck in layout.grain) {
      if (progress < 1 && speck.dx > revealX + layout.fontSizePx * 0.3) {
        continue;
      }
      paint.color = layout.color.withValues(
        alpha: (speck.alpha * alpha).clamp(0.0, 1.0),
      );
      canvas.drawCircle(
        Offset(speck.dx, speck.dy),
        speck.radius,
        paint,
      );
    }
    if (progress < 1) {
      // Chalk dust band right at the drawing edge: a few short dry strokes.
      final edgePaint = Paint()
        ..strokeWidth = math.max(0.7, layout.fontSizePx * 0.035)
        ..strokeCap = StrokeCap.round
        ..color = layout.color.withValues(alpha: 0.16 * alpha);
      for (var i = 0; i < 5; i++) {
        final y = layout.fontSizePx * 0.25 +
            (layout.height - layout.fontSizePx * 0.4) * (i / 4);
        final length = layout.fontSizePx * (0.22 + (i % 3) * 0.12);
        final startX = revealX - length * 0.5;
        canvas.drawLine(
          Offset(startX, y),
          Offset(startX + length, y + layout.fontSizePx * 0.02),
          edgePaint,
        );
      }
    }
  }

  void _paintUnderline(
    ui.Canvas canvas,
    ChalkTextLayout layout,
    double progress,
    double alpha,
  ) {
    final width = layout.width * progress;
    final path = Path();
    final y = layout.painter.height + layout.fontSizePx * 0.12;
    final steps = math.max(2, (width / (layout.fontSizePx * 0.55)).ceil());
    for (var i = 0; i <= steps; i++) {
      final t = i / steps;
      final x = width * t;
      final wobble = math.sin(t * math.pi * 4.3) * layout.fontSizePx * 0.04;
      if (i == 0) {
        path.moveTo(0, y + wobble);
      } else {
        path.lineTo(x, y + wobble);
      }
    }
    final paint = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = math.max(1.0, layout.fontSizePx * 0.055)
      ..strokeCap = StrokeCap.round
      ..color = layout.color.withValues(alpha: 0.8 * alpha);
    canvas.drawPath(path, paint);
  }

  void clear() {
    for (final layout in _cache.values) {
      layout.dispose();
    }
    _cache.clear();
    _lru.clear();
  }

  void dispose() => clear();

  void _touch(String key) {
    _lru.remove(key);
    _lru.add(key);
  }

  TextStyle _textStyle({
    required ChalkTextStyleSpec spec,
    required Color color,
    required double fontSizePx,
  }) {
    final family = spec.monospace ? 'monospace' : ChalkFonts.script;
    return TextStyle(
      color: color,
      fontSize: fontSizePx,
      fontWeight: spec.weight,
      letterSpacing: fontSizePx * spec.letterSpacingEm,
      height: spec.lineHeight,
      fontFamily: family,
      fontFamilyFallback:
          spec.monospace ? ChalkFonts.monospaceFallback : ChalkFonts.fallback,
    );
  }

  static List<ChalkSpeck> _generateGrain(
    String text,
    ChalkTextStyleSpec spec,
    double width,
    double height,
    double fontSizePx,
  ) {
    if (width <= 0 || height <= 0) return const <ChalkSpeck>[];
    final random = math.Random(_stableHash('${text.length}|${spec.cacheKey}'));
    final count = (text.length * 1.6 + 8).clamp(10, 120).round();
    final specks = <ChalkSpeck>[];
    for (var i = 0; i < count; i++) {
      final dx = random.nextDouble() * width;
      final dy = random.nextDouble() * height;
      final radius = (0.35 + random.nextDouble() * 1.1) *
          (fontSizePx / 32).clamp(0.6, 2.2);
      final alpha = 0.04 + random.nextDouble() * 0.13;
      specks.add(ChalkSpeck(dx, dy, radius, alpha));
    }
    // A few larger dry patches where the chalk skipped.
    for (var i = 0; i < 4; i++) {
      specks.add(
        ChalkSpeck(
          random.nextDouble() * width,
          random.nextDouble() * height,
          (fontSizePx * 0.02) * (0.5 + random.nextDouble()),
          0.035,
        ),
      );
    }
    return List<ChalkSpeck>.unmodifiable(specks);
  }

  static int _stableHash(String value) {
    var hash = 0x811c9dc5;
    for (var i = 0; i < value.length; i++) {
      hash ^= value.codeUnitAt(i);
      hash = (hash * 0x01000193) & 0x7fffffff;
    }
    return hash;
  }
}

/// Normalized geometry helpers shared by text-rendering call sites.
class ChalkGeometry {
  const ChalkGeometry._();

  /// A hand-drawn-ish rectangle path with deterministic corner jitter.
  static Path wobblyRect(Rect rect, {double jitter = 0.6, int seed = 7}) {
    final random = math.Random(seed);
    double j() => (random.nextDouble() - 0.5) * jitter * 2;
    final path = Path();
    final topLeft = Offset(rect.left + j(), rect.top + j());
    final topRight = Offset(rect.right + j(), rect.top + j());
    final bottomRight = Offset(rect.right + j(), rect.bottom + j());
    final bottomLeft = Offset(rect.left + j(), rect.bottom + j());
    path.moveTo(topLeft.dx, topLeft.dy);
    path.quadraticBezierTo(
      rect.center.dx + j(),
      rect.top + j(),
      topRight.dx,
      topRight.dy,
    );
    path.quadraticBezierTo(
      rect.right + j(),
      rect.center.dy + j(),
      bottomRight.dx,
      bottomRight.dy,
    );
    path.quadraticBezierTo(
      rect.center.dx + j(),
      rect.bottom + j(),
      bottomLeft.dx,
      bottomLeft.dy,
    );
    path.quadraticBezierTo(
      rect.left + j(),
      rect.center.dy + j(),
      topLeft.dx,
      topLeft.dy,
    );
    return path;
  }

  /// Hand-drawn ellipse-ish loop (two wobbly halves) for chalk circles.
  static Path wobblyEllipse(Rect rect, {double jitter = 1.2, int seed = 11}) {
    final random = math.Random(seed);
    double j() => (random.nextDouble() - 0.5) * jitter * 2;
    final center = rect.center;
    final path = Path();
    final start = Offset(rect.left + j(), center.dy + j());
    path.moveTo(start.dx, start.dy);
    path.cubicTo(
      rect.left + j(),
      rect.top + j(),
      rect.right + j(),
      rect.top + j(),
      rect.right + j(),
      center.dy + j(),
    );
    path.cubicTo(
      rect.right + j(),
      rect.bottom + j(),
      rect.left + j(),
      rect.bottom + j(),
      start.dx,
      start.dy,
    );
    return path;
  }

  /// Builds a wavy underline path (for highlight/underline annotations).
  static Path wavyLine(Offset start, Offset end, {double amplitude = 1.4}) {
    final path = Path()..moveTo(start.dx, start.dy);
    final length = (end - start).distance;
    final steps = math.max(2, (length / 26).ceil());
    final direction = (end - start) / math.max(length, 1);
    final normal = Offset(-direction.dy, direction.dx);
    for (var i = 1; i <= steps; i++) {
      final t = i / steps;
      final wave = math.sin(t * math.pi * steps * 0.9) * amplitude;
      final point = start + (end - start) * t + normal * wave;
      path.lineTo(point.dx, point.dy);
    }
    return path;
  }

  /// Dashes a path using its metrics (Flutter has no built-in dash pattern).
  static Path dashedPath(Path source, List<double> dashPattern) {
    if (dashPattern.isEmpty) return source;
    final pattern = dashPattern
        .where((value) => value.isFinite && value > 0)
        .toList(growable: false);
    if (pattern.isEmpty) return source;
    final dashed = Path();
    for (final metric in source.computeMetrics()) {
      var distance = 0.0;
      var index = 0;
      while (distance < metric.length) {
        final length = pattern[index % pattern.length];
        final end = math.min(distance + length, metric.length);
        if (index.isEven) {
          dashed.addPath(metric.extractPath(distance, end), Offset.zero);
        }
        distance = end;
        index++;
      }
    }
    return dashed;
  }

  /// Returns the visible prefix of [path] for reveal progress in `[0, 1]`.
  static Path revealPath(Path path, double progress) {
    if (progress >= 1) return path;
    if (progress <= 0) return Path();
    final metrics = path.computeMetrics().toList(growable: false);
    final total = metrics.fold<double>(0, (sum, metric) => sum + metric.length);
    if (total <= 0) return path;
    var remaining = total * progress.clamp(0.0, 1.0);
    final revealed = Path();
    for (final metric in metrics) {
      if (remaining <= 0) break;
      final take = math.min(remaining, metric.length);
      revealed.addPath(metric.extractPath(0, take), Offset.zero);
      remaining -= take;
    }
    return revealed;
  }

  /// Normalized bounds of a pixel rect, clamped to the board.
  static BoardRect normalizedBounds(Rect pixelRect, Rect contentRect) {
    if (contentRect.width <= 0 || contentRect.height <= 0) {
      return BoardRect.zero;
    }
    return BoardRect(
      (pixelRect.left - contentRect.left) / contentRect.width,
      (pixelRect.top - contentRect.top) / contentRect.height,
      pixelRect.width / contentRect.width,
      pixelRect.height / contentRect.height,
    ).clamp01();
  }
}
