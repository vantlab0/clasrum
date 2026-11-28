/// Generative drawing protocol model (clasrum.drawing v1).
///
/// Mirrors the backend protocol in `backend/app/board/drawing_protocol.py`.
/// The model is strict about known fields and tolerant of unknown top-level
/// keys where the backend is forward-compatible. Malformed elements are
/// surfaced as [DrawingPlanParseException] rather than silently ignored.
library;

import 'dart:math' as math;
import 'dart:ui' show Color, Offset;

import 'board_geometry.dart';
import 'chalk_palette.dart';

const String kDrawingProtocolName = 'clasrum.drawing';
const int kDrawingProtocolVersion = 1;

const int kDrawingMaxElements = 120;
const int kDrawingMaxStages = 12;
const int kDrawingMaxPathSegments = 160;
const int kDrawingMaxPoints = 400;
const int kDrawingMaxDashSegments = 16;
const int kDrawingMaxTextLength = 500;
const int kDrawingMinDurationMs = 60;
const int kDrawingMaxDurationMs = 6000;
const int kDrawingMaxDelayMs = 4000;
const int kDrawingMaxPauseMs = 3000;

final RegExp _elementIdRe = RegExp(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$');
final RegExp _hexColorRe = RegExp(r'^#[0-9a-fA-F]{6}$');
final RegExp _controlCharsRe = RegExp(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]');

const Set<String> kChalkRoles = {
  'primary',
  'secondary',
  'blue',
  'yellow',
  'red',
  'green',
};

class DrawingPlanParseException implements Exception {
  DrawingPlanParseException(this.message, {this.path});

  final String message;
  final String? path;

  @override
  String toString() => path == null
      ? 'DrawingPlanParseException: $message'
      : 'DrawingPlanParseException($path): $message';
}

double? _num(Object? value) {
  if (value is bool) return null;
  if (value is num) {
    final d = value.toDouble();
    return d.isFinite ? d : null;
  }
  if (value is String) {
    final d = double.tryParse(value.trim());
    return d != null && d.isFinite ? d : null;
  }
  return null;
}

int? _int(Object? value) => _num(value)?.round();

String? _str(Object? value) {
  if (value == null) return null;
  if (value is String) return value;
  return value.toString();
}

Map<String, dynamic>? _map(Object? value) {
  if (value is Map<String, dynamic>) return value;
  if (value is Map) return value.map((k, v) => MapEntry(k.toString(), v));
  return null;
}

List<dynamic>? _list(Object? value) => value is List ? value : null;

int _clampInt(int v, int min, int max) => v < min ? min : (v > max ? max : v);

double _round4(double v) {
  final r = double.parse(v.toStringAsFixed(4));
  return r == 0 ? 0.0 : r;
}

String _normalizeColor(String raw, {String path = 'color'}) {
  final trimmed = raw.trim();
  final lowered = trimmed.toLowerCase();
  if (kChalkRoles.contains(lowered)) return lowered;
  if (_hexColorRe.hasMatch(trimmed)) return '#${trimmed.substring(1).toUpperCase()}';
  throw DrawingPlanParseException(
    'color must be a chalk role name or #RRGGBB hex string',
    path: path,
  );
}

class ProtocolPoint {
  const ProtocolPoint(this.x, this.y);

  final double x;
  final double y;

  factory ProtocolPoint.parse(Object? value, {String path = 'point'}) {
    final map = _map(value);
    if (map == null) {
      throw DrawingPlanParseException('must be an object', path: path);
    }
    final x = _num(map['x']);
    final y = _num(map['y']);
    if (x == null || y == null) {
      throw DrawingPlanParseException('x and y must be finite numbers', path: path);
    }
    if (x < 0 || x > 1 || y < 0 || y > 1) {
      throw DrawingPlanParseException('x and y must be in [0, 1]', path: path);
    }
    return ProtocolPoint(_round4(x), _round4(y));
  }

  Map<String, dynamic> toJson() => {'x': _round4(x), 'y': _round4(y)};
  Offset toOffset() => Offset(x, y);
  BoardPoint toBoardPoint() => BoardPoint(x, y);
}

class ProtocolSize {
  const ProtocolSize(this.w, this.h);

  final double w;
  final double h;

  factory ProtocolSize.parse(Object? value, {String path = 'size'}) {
    final map = _map(value);
    if (map == null) {
      throw DrawingPlanParseException('must be an object', path: path);
    }
    final w = _num(map['w']);
    final h = _num(map['h']);
    if (w == null || h == null) {
      throw DrawingPlanParseException('w and h must be finite numbers', path: path);
    }
    if (w <= 0 || w > 1 || h <= 0 || h > 1) {
      throw DrawingPlanParseException('w and h must be in (0, 1]', path: path);
    }
    return ProtocolSize(_round4(w), _round4(h));
  }

  Map<String, dynamic> toJson() => {'w': _round4(w), 'h': _round4(h)};
}

class ProtocolStyle {
  const ProtocolStyle({
    this.strokeColor,
    this.strokeWidth,
    this.strokeOpacity,
    this.fillColor,
    this.fillOpacity,
    this.lineCap,
    this.lineJoin,
    this.dash,
  });

  final String? strokeColor;
  final double? strokeWidth;
  final double? strokeOpacity;
  final String? fillColor;
  final double? fillOpacity;
  final String? lineCap; // butt|round|square
  final String? lineJoin; // miter|round|bevel
  final List<double>? dash;

  factory ProtocolStyle.parse(Object? value, {String path = 'style'}) {
    final map = _map(value);
    if (map == null) {
      throw DrawingPlanParseException('must be an object', path: path);
    }
    String? strokeColor;
    String? fillColor;
    final rawStroke = _str(map['stroke_color']);
    final rawFill = _str(map['fill_color']);
    if (rawStroke != null) strokeColor = _normalizeColor(rawStroke, path: '$path.stroke_color');
    if (rawFill != null) fillColor = _normalizeColor(rawFill, path: '$path.fill_color');

    double? strokeWidth = _num(map['stroke_width']);
    if (strokeWidth != null && (strokeWidth < 0.2 || strokeWidth > 4.0)) {
      throw DrawingPlanParseException('stroke_width must be in [0.2, 4.0]', path: '$path.stroke_width');
    }
    double? strokeOpacity = _num(map['stroke_opacity']);
    if (strokeOpacity != null && (strokeOpacity < 0 || strokeOpacity > 1)) {
      throw DrawingPlanParseException('stroke_opacity must be in [0, 1]', path: '$path.stroke_opacity');
    }
    double? fillOpacity = _num(map['fill_opacity']);
    if (fillOpacity != null && (fillOpacity < 0 || fillOpacity > 1)) {
      throw DrawingPlanParseException('fill_opacity must be in [0, 1]', path: '$path.fill_opacity');
    }

    final lineCap = _str(map['line_cap']);
    if (lineCap != null && !const {'butt', 'round', 'square'}.contains(lineCap)) {
      throw DrawingPlanParseException('invalid line_cap', path: '$path.line_cap');
    }
    final lineJoin = _str(map['line_join']);
    if (lineJoin != null && !const {'miter', 'round', 'bevel'}.contains(lineJoin)) {
      throw DrawingPlanParseException('invalid line_join', path: '$path.line_join');
    }

    List<double>? dash;
    final rawDash = map['dash'];
    if (rawDash != null) {
      final list = _list(rawDash);
      if (list == null || list.isEmpty || list.length > kDrawingMaxDashSegments) {
        throw DrawingPlanParseException(
          'dash must be a list with 1..$kDrawingMaxDashSegments numbers',
          path: '$path.dash',
        );
      }
      dash = <double>[];
      for (var i = 0; i < list.length; i++) {
        final v = _num(list[i]);
        if (v == null || v < 0.05 || v > 50.0) {
          throw DrawingPlanParseException('dash values must be in [0.05, 50]', path: '$path.dash[$i]');
        }
        dash.add(_round4(v));
      }
    }

    return ProtocolStyle(
      strokeColor: strokeColor,
      strokeWidth: strokeWidth == null ? null : _round4(strokeWidth),
      strokeOpacity: strokeOpacity == null ? null : _round4(strokeOpacity),
      fillColor: fillColor,
      fillOpacity: fillOpacity == null ? null : _round4(fillOpacity),
      lineCap: lineCap,
      lineJoin: lineJoin,
      dash: dash == null ? null : List<double>.unmodifiable(dash),
    );
  }

  Map<String, dynamic> toJson() => <String, dynamic>{
        if (strokeColor != null) 'stroke_color': strokeColor,
        if (strokeWidth != null) 'stroke_width': _round4(strokeWidth!),
        if (strokeOpacity != null) 'stroke_opacity': _round4(strokeOpacity!),
        if (fillColor != null) 'fill_color': fillColor,
        if (fillOpacity != null) 'fill_opacity': _round4(fillOpacity!),
        if (lineCap != null) 'line_cap': lineCap,
        if (lineJoin != null) 'line_join': lineJoin,
        if (dash != null) 'dash': dash!.map(_round4).toList(),
      };

  /// Illustration colours are always snapped to the bright board palette.
  /// The [fallback] argument is kept for API compatibility; when no explicit
  /// colour exists it is itself snapped, so legacy chalk roles cannot leak into
  /// a diagram as dark/gray ink.
  Color resolveStrokeColor({Color fallback = ChalkPalette.primary}) =>
      ChalkPalette.resolveIllustration(strokeColor ?? fallback, fallbackRole: 'cyan');

  Color resolveFillColor({Color fallback = ChalkPalette.primary}) =>
      ChalkPalette.resolveIllustration(fillColor ?? fallback, fallbackRole: 'teal');
}

// ---------------------------------------------------------------------------
// Geometry
// ---------------------------------------------------------------------------

enum DrawingElementKind {
  line,
  polyline,
  polygon,
  rectangle,
  ellipse,
  arc,
  quadBezier,
  cubicBezier,
  path,
  text,
  arrow;

  static DrawingElementKind parse(String raw, {String path = 'kind'}) {
    switch (raw) {
      case 'line':
        return DrawingElementKind.line;
      case 'polyline':
        return DrawingElementKind.polyline;
      case 'polygon':
        return DrawingElementKind.polygon;
      case 'rectangle':
        return DrawingElementKind.rectangle;
      case 'ellipse':
        return DrawingElementKind.ellipse;
      case 'arc':
        return DrawingElementKind.arc;
      case 'quad_bezier':
        return DrawingElementKind.quadBezier;
      case 'cubic_bezier':
        return DrawingElementKind.cubicBezier;
      case 'path':
        return DrawingElementKind.path;
      case 'text':
        return DrawingElementKind.text;
      case 'arrow':
        return DrawingElementKind.arrow;
      default:
        throw DrawingPlanParseException('unsupported element kind "$raw"', path: path);
    }
  }

  String get wireName {
    switch (this) {
      case DrawingElementKind.line:
        return 'line';
      case DrawingElementKind.polyline:
        return 'polyline';
      case DrawingElementKind.polygon:
        return 'polygon';
      case DrawingElementKind.rectangle:
        return 'rectangle';
      case DrawingElementKind.ellipse:
        return 'ellipse';
      case DrawingElementKind.arc:
        return 'arc';
      case DrawingElementKind.quadBezier:
        return 'quad_bezier';
      case DrawingElementKind.cubicBezier:
        return 'cubic_bezier';
      case DrawingElementKind.path:
        return 'path';
      case DrawingElementKind.text:
        return 'text';
      case DrawingElementKind.arrow:
        return 'arrow';
    }
  }

  bool get isClosed {
    switch (this) {
      case DrawingElementKind.polygon:
      case DrawingElementKind.rectangle:
      case DrawingElementKind.ellipse:
        return true;
      default:
        return false;
    }
  }
}

abstract class DrawingGeometry {
  const DrawingGeometry();

  DrawingElementKind get kind;
  Map<String, dynamic> toJson();
  BoardRect bounds();
}

class LineGeometry extends DrawingGeometry {
  const LineGeometry({required this.start, required this.end});

  final ProtocolPoint start;
  final ProtocolPoint end;

  @override
  DrawingElementKind get kind => DrawingElementKind.line;

  factory LineGeometry.parse(Object? value, {String path = 'geometry'}) {
    final map = _map(value);
    if (map == null) throw DrawingPlanParseException('must be an object', path: path);
    return LineGeometry(
      start: ProtocolPoint.parse(map['start'], path: '$path.start'),
      end: ProtocolPoint.parse(map['end'], path: '$path.end'),
    );
  }

  @override
  Map<String, dynamic> toJson() => {'start': start.toJson(), 'end': end.toJson()};

  @override
  BoardRect bounds() => BoardRect.fromPoints(start.toBoardPoint(), end.toBoardPoint());
}

class PolylineGeometry extends DrawingGeometry {
  const PolylineGeometry({
    required this.points,
    this.closed = false,
    this.smooth = true,
  });

  final List<ProtocolPoint> points;
  final bool closed;
  final bool smooth;

  @override
  DrawingElementKind get kind => closed ? DrawingElementKind.polygon : DrawingElementKind.polyline;

  factory PolylineGeometry.parse(Object? value, {String path = 'geometry'}) {
    final map = _map(value);
    if (map == null) throw DrawingPlanParseException('must be an object', path: path);
    final rawPoints = _list(map['points']);
    if (rawPoints == null || rawPoints.length < 2 || rawPoints.length > kDrawingMaxPoints) {
      throw DrawingPlanParseException('points must have 2..$kDrawingMaxPoints items', path: '$path.points');
    }
    final points = <ProtocolPoint>[];
    for (var i = 0; i < rawPoints.length; i++) {
      points.add(ProtocolPoint.parse(rawPoints[i], path: '$path.points[$i]'));
    }
    return PolylineGeometry(
      points: List<ProtocolPoint>.unmodifiable(points),
      closed: map['closed'] == true,
      smooth: map['smooth'] != false,
    );
  }

  @override
  Map<String, dynamic> toJson() => {
        'points': points.map((p) => p.toJson()).toList(),
        'closed': closed,
        'smooth': smooth,
      };

  @override
  BoardRect bounds() => _boundsFromPoints(points.map((p) => p.toBoardPoint()));
}

class RectangleGeometry extends DrawingGeometry {
  const RectangleGeometry({required this.position, required this.size, this.cornerRadius});

  final ProtocolPoint position;
  final ProtocolSize size;
  final double? cornerRadius;

  @override
  DrawingElementKind get kind => DrawingElementKind.rectangle;

  factory RectangleGeometry.parse(Object? value, {String path = 'geometry'}) {
    final map = _map(value);
    if (map == null) throw DrawingPlanParseException('must be an object', path: path);
    double? cornerRadius = _num(map['corner_radius']);
    if (cornerRadius != null && (cornerRadius < 0 || cornerRadius > 0.5)) {
      throw DrawingPlanParseException('corner_radius must be in [0, 0.5]', path: '$path.corner_radius');
    }
    return RectangleGeometry(
      position: ProtocolPoint.parse(map['position'], path: '$path.position'),
      size: ProtocolSize.parse(map['size'], path: '$path.size'),
      cornerRadius: cornerRadius == null ? null : _round4(cornerRadius),
    );
  }

  @override
  Map<String, dynamic> toJson() => {
        'position': position.toJson(),
        'size': size.toJson(),
        if (cornerRadius != null) 'corner_radius': _round4(cornerRadius!),
      };

  @override
  BoardRect bounds() => BoardRect(position.x, position.y, size.w, size.h);
}

class EllipseGeometry extends DrawingGeometry {
  const EllipseGeometry({required this.center, required this.rx, required this.ry});

  final ProtocolPoint center;
  final double rx;
  final double ry;

  @override
  DrawingElementKind get kind => DrawingElementKind.ellipse;

  factory EllipseGeometry.parse(Object? value, {String path = 'geometry'}) {
    final map = _map(value);
    if (map == null) throw DrawingPlanParseException('must be an object', path: path);
    final rx = _num(map['rx']);
    final ry = _num(map['ry']);
    if (rx == null || ry == null || rx <= 0 || rx > 1 || ry <= 0 || ry > 1) {
      throw DrawingPlanParseException('rx and ry must be in (0, 1]', path: path);
    }
    return EllipseGeometry(
      center: ProtocolPoint.parse(map['center'], path: '$path.center'),
      rx: _round4(rx),
      ry: _round4(ry),
    );
  }

  @override
  Map<String, dynamic> toJson() => {'center': center.toJson(), 'rx': _round4(rx), 'ry': _round4(ry)};

  @override
  BoardRect bounds() => BoardRect(center.x - rx, center.y - ry, rx * 2, ry * 2);
}

class ArcGeometry extends DrawingGeometry {
  const ArcGeometry({
    required this.center,
    required this.rx,
    required this.ry,
    required this.startAngle,
    required this.sweepAngle,
  });

  final ProtocolPoint center;
  final double rx;
  final double ry;
  final double startAngle;
  final double sweepAngle;

  @override
  DrawingElementKind get kind => DrawingElementKind.arc;

  factory ArcGeometry.parse(Object? value, {String path = 'geometry'}) {
    final map = _map(value);
    if (map == null) throw DrawingPlanParseException('must be an object', path: path);
    final rx = _num(map['rx']);
    final ry = _num(map['ry']);
    final startAngle = _num(map['start_angle']);
    final sweepAngle = _num(map['sweep_angle']);
    if (rx == null || ry == null || rx <= 0 || rx > 1 || ry <= 0 || ry > 1) {
      throw DrawingPlanParseException('rx and ry must be in (0, 1]', path: path);
    }
    if (startAngle == null || sweepAngle == null || startAngle.abs() > 720 || sweepAngle.abs() > 720) {
      throw DrawingPlanParseException('angles must be in [-720, 720]', path: path);
    }
    return ArcGeometry(
      center: ProtocolPoint.parse(map['center'], path: '$path.center'),
      rx: _round4(rx),
      ry: _round4(ry),
      startAngle: _round4(startAngle),
      sweepAngle: _round4(sweepAngle),
    );
  }

  @override
  Map<String, dynamic> toJson() => {
        'center': center.toJson(),
        'rx': _round4(rx),
        'ry': _round4(ry),
        'start_angle': _round4(startAngle),
        'sweep_angle': _round4(sweepAngle),
      };

  @override
  BoardRect bounds() => BoardRect(center.x - rx, center.y - ry, rx * 2, ry * 2);
}

class QuadBezierGeometry extends DrawingGeometry {
  const QuadBezierGeometry({required this.start, required this.control, required this.end});

  final ProtocolPoint start;
  final ProtocolPoint control;
  final ProtocolPoint end;

  @override
  DrawingElementKind get kind => DrawingElementKind.quadBezier;

  factory QuadBezierGeometry.parse(Object? value, {String path = 'geometry'}) {
    final map = _map(value);
    if (map == null) throw DrawingPlanParseException('must be an object', path: path);
    if (map['control'] == null) {
      throw DrawingPlanParseException('quad_bezier requires control', path: '$path.control');
    }
    return QuadBezierGeometry(
      start: ProtocolPoint.parse(map['start'], path: '$path.start'),
      control: ProtocolPoint.parse(map['control'], path: '$path.control'),
      end: ProtocolPoint.parse(map['end'], path: '$path.end'),
    );
  }

  @override
  Map<String, dynamic> toJson() => {
        'start': start.toJson(),
        'control': control.toJson(),
        'end': end.toJson(),
      };

  @override
  BoardRect bounds() => _boundsFromPoints([start.toBoardPoint(), control.toBoardPoint(), end.toBoardPoint()]);
}

class CubicBezierGeometry extends DrawingGeometry {
  const CubicBezierGeometry({
    required this.start,
    required this.c1,
    required this.c2,
    required this.end,
  });

  final ProtocolPoint start;
  final ProtocolPoint c1;
  final ProtocolPoint c2;
  final ProtocolPoint end;

  @override
  DrawingElementKind get kind => DrawingElementKind.cubicBezier;

  factory CubicBezierGeometry.parse(Object? value, {String path = 'geometry'}) {
    final map = _map(value);
    if (map == null) throw DrawingPlanParseException('must be an object', path: path);
    if (map['c1'] == null || map['c2'] == null) {
      throw DrawingPlanParseException('cubic_bezier requires c1 and c2', path: path);
    }
    return CubicBezierGeometry(
      start: ProtocolPoint.parse(map['start'], path: '$path.start'),
      c1: ProtocolPoint.parse(map['c1'], path: '$path.c1'),
      c2: ProtocolPoint.parse(map['c2'], path: '$path.c2'),
      end: ProtocolPoint.parse(map['end'], path: '$path.end'),
    );
  }

  @override
  Map<String, dynamic> toJson() => {
        'start': start.toJson(),
        'c1': c1.toJson(),
        'c2': c2.toJson(),
        'end': end.toJson(),
      };

  @override
  BoardRect bounds() => _boundsFromPoints([
        start.toBoardPoint(),
        c1.toBoardPoint(),
        c2.toBoardPoint(),
        end.toBoardPoint(),
      ]);
}

class PathSegment {
  const PathSegment._({
    required this.op,
    this.point,
    this.control,
    this.c1,
    this.c2,
    this.center,
    this.rx,
    this.ry,
    this.startAngle,
    this.sweepAngle,
  });

  final String op; // move|line|quad|cubic|arc|close
  final ProtocolPoint? point;
  final ProtocolPoint? control;
  final ProtocolPoint? c1;
  final ProtocolPoint? c2;
  final ProtocolPoint? center;
  final double? rx;
  final double? ry;
  final double? startAngle;
  final double? sweepAngle;

  factory PathSegment.parse(Object? value, {String path = 'segment'}) {
    final map = _map(value);
    if (map == null) throw DrawingPlanParseException('must be an object', path: path);
    final op = _str(map['op']);
    if (op == null || !const {'move', 'line', 'quad', 'cubic', 'arc', 'close'}.contains(op)) {
      throw DrawingPlanParseException('invalid path op "$op"', path: '$path.op');
    }
    ProtocolPoint? point;
    ProtocolPoint? control;
    ProtocolPoint? c1;
    ProtocolPoint? c2;
    ProtocolPoint? center;
    double? rx;
    double? ry;
    double? startAngle;
    double? sweepAngle;

    if (op == 'move' || op == 'line') {
      point = ProtocolPoint.parse(map['point'], path: '$path.point');
    } else if (op == 'quad') {
      control = ProtocolPoint.parse(map['control'], path: '$path.control');
      point = ProtocolPoint.parse(map['point'], path: '$path.point');
    } else if (op == 'cubic') {
      c1 = ProtocolPoint.parse(map['c1'], path: '$path.c1');
      c2 = ProtocolPoint.parse(map['c2'], path: '$path.c2');
      point = ProtocolPoint.parse(map['point'], path: '$path.point');
    } else if (op == 'arc') {
      center = ProtocolPoint.parse(map['center'], path: '$path.center');
      rx = _num(map['rx']);
      ry = _num(map['ry']);
      startAngle = _num(map['start_angle']);
      sweepAngle = _num(map['sweep_angle']);
      if (rx == null || ry == null || rx <= 0 || rx > 1 || ry <= 0 || ry > 1) {
        throw DrawingPlanParseException('arc rx/ry must be in (0, 1]', path: path);
      }
      if (startAngle == null || sweepAngle == null || startAngle.abs() > 720 || sweepAngle.abs() > 720) {
        throw DrawingPlanParseException('arc angles must be in [-720, 720]', path: path);
      }
      rx = _round4(rx);
      ry = _round4(ry);
      startAngle = _round4(startAngle);
      sweepAngle = _round4(sweepAngle);
    }
    return PathSegment._(
      op: op,
      point: point,
      control: control,
      c1: c1,
      c2: c2,
      center: center,
      rx: rx,
      ry: ry,
      startAngle: startAngle,
      sweepAngle: sweepAngle,
    );
  }

  Map<String, dynamic> toJson() => <String, dynamic>{
        'op': op,
        if (point != null) 'point': point!.toJson(),
        if (control != null) 'control': control!.toJson(),
        if (c1 != null) 'c1': c1!.toJson(),
        if (c2 != null) 'c2': c2!.toJson(),
        if (center != null) 'center': center!.toJson(),
        if (rx != null) 'rx': _round4(rx!),
        if (ry != null) 'ry': _round4(ry!),
        if (startAngle != null) 'start_angle': _round4(startAngle!),
        if (sweepAngle != null) 'sweep_angle': _round4(sweepAngle!),
      };
}

class PathGeometry extends DrawingGeometry {
  const PathGeometry({required this.segments});

  final List<PathSegment> segments;

  @override
  DrawingElementKind get kind => DrawingElementKind.path;

  factory PathGeometry.parse(Object? value, {String path = 'geometry'}) {
    final map = _map(value);
    if (map == null) throw DrawingPlanParseException('must be an object', path: path);
    final rawSegments = _list(map['segments']);
    if (rawSegments == null || rawSegments.isEmpty || rawSegments.length > kDrawingMaxPathSegments) {
      throw DrawingPlanParseException(
        'segments must have 1..$kDrawingMaxPathSegments items',
        path: '$path.segments',
      );
    }
    final segments = <PathSegment>[];
    for (var i = 0; i < rawSegments.length; i++) {
      segments.add(PathSegment.parse(rawSegments[i], path: '$path.segments[$i]'));
    }
    if (segments.first.op == 'close') {
      throw DrawingPlanParseException('path must not start with close', path: '$path.segments[0]');
    }
    return PathGeometry(segments: List<PathSegment>.unmodifiable(segments));
  }

  bool get hasClose => segments.any((s) => s.op == 'close');

  @override
  Map<String, dynamic> toJson() => {'segments': segments.map((s) => s.toJson()).toList()};

  @override
  BoardRect bounds() {
    final points = <BoardPoint>[];
    for (final seg in segments) {
      if (seg.point != null) points.add(seg.point!.toBoardPoint());
      if (seg.control != null) points.add(seg.control!.toBoardPoint());
      if (seg.c1 != null) points.add(seg.c1!.toBoardPoint());
      if (seg.c2 != null) points.add(seg.c2!.toBoardPoint());
      if (seg.center != null) {
        final c = seg.center!;
        final rx = seg.rx ?? 0;
        final ry = seg.ry ?? 0;
        points.add(BoardPoint(c.x - rx, c.y - ry));
        points.add(BoardPoint(c.x + rx, c.y + ry));
      }
    }
    return _boundsFromPoints(points);
  }
}

class TextGeometry extends DrawingGeometry {
  const TextGeometry({
    required this.text,
    required this.position,
    this.size,
    this.align = 'left',
    this.fontScale,
  });

  final String text;
  final ProtocolPoint position;
  final ProtocolSize? size;
  final String align;
  final double? fontScale;

  @override
  DrawingElementKind get kind => DrawingElementKind.text;

  factory TextGeometry.parse(Object? value, {String path = 'geometry'}) {
    final map = _map(value);
    if (map == null) throw DrawingPlanParseException('must be an object', path: path);
    final text = _str(map['text']);
    if (text == null || text.isEmpty || text.length > kDrawingMaxTextLength) {
      throw DrawingPlanParseException('text must be 1..$kDrawingMaxTextLength chars', path: '$path.text');
    }
    if (_controlCharsRe.hasMatch(text)) {
      throw DrawingPlanParseException('text must not contain control characters', path: '$path.text');
    }
    final align = _str(map['align']) ?? 'left';
    if (!const {'left', 'center', 'right'}.contains(align)) {
      throw DrawingPlanParseException('invalid align', path: '$path.align');
    }
    final fontScale = _num(map['font_scale']);
    if (fontScale != null && (fontScale < 0.5 || fontScale > 2.5)) {
      throw DrawingPlanParseException('font_scale must be in [0.5, 2.5]', path: '$path.font_scale');
    }
    return TextGeometry(
      text: text,
      position: ProtocolPoint.parse(map['position'], path: '$path.position'),
      size: map['size'] == null ? null : ProtocolSize.parse(map['size'], path: '$path.size'),
      align: align,
      fontScale: fontScale == null ? null : _round4(fontScale),
    );
  }

  @override
  Map<String, dynamic> toJson() => {
        'text': text,
        'position': position.toJson(),
        'align': align,
        if (size != null) 'size': size!.toJson(),
        if (fontScale != null) 'font_scale': _round4(fontScale!),
      };

  @override
  BoardRect bounds() {
    if (size != null) return BoardRect(position.x, position.y, size!.w, size!.h);
    final width = math.min(0.9, math.max(0.03, text.length * 0.008));
    return BoardRect(position.x, position.y, width, 0.03);
  }
}

class ArrowGeometry extends DrawingGeometry {
  const ArrowGeometry({required this.start, required this.end, this.curve = 0.0});

  final ProtocolPoint start;
  final ProtocolPoint end;
  final double curve;

  @override
  DrawingElementKind get kind => DrawingElementKind.arrow;

  factory ArrowGeometry.parse(Object? value, {String path = 'geometry'}) {
    final map = _map(value);
    if (map == null) throw DrawingPlanParseException('must be an object', path: path);
    final curve = _num(map['curve']) ?? 0.0;
    if (curve < -1.0 || curve > 1.0) {
      throw DrawingPlanParseException('curve must be in [-1, 1]', path: '$path.curve');
    }
    return ArrowGeometry(
      start: ProtocolPoint.parse(map['start'], path: '$path.start'),
      end: ProtocolPoint.parse(map['end'], path: '$path.end'),
      curve: _round4(curve),
    );
  }

  @override
  Map<String, dynamic> toJson() => {
        'start': start.toJson(),
        'end': end.toJson(),
        'curve': _round4(curve),
      };

  @override
  BoardRect bounds() {
    final points = <BoardPoint>[start.toBoardPoint(), end.toBoardPoint()];
    if (curve.abs() > 1e-6) {
      final x1 = start.x, y1 = start.y, x2 = end.x, y2 = end.y;
      points.add(BoardPoint(
        x1 + (x2 - x1) * 0.5 + curve * (y1 - y2) * 0.3,
        y1 + (y2 - y1) * 0.5 + curve * (x2 - x1) * 0.3,
      ));
    }
    return _boundsFromPoints(points);
  }
}

// ---------------------------------------------------------------------------
// Element / Stage / Plan
// ---------------------------------------------------------------------------

class DrawingElement {
  const DrawingElement({
    required this.id,
    required this.kind,
    required this.geometry,
    this.style,
    this.layer = 0,
    this.stage = 1,
    this.durationMs = 600,
    this.delayMs = 0,
    this.clipId,
  });

  final String id;
  final DrawingElementKind kind;
  final DrawingGeometry geometry;
  final ProtocolStyle? style;
  final int layer;
  final int stage;
  final int durationMs;
  final int delayMs;
  final String? clipId;

  bool get isClosed {
    if (kind.isClosed) return true;
    if (kind == DrawingElementKind.polyline) {
      final g = geometry;
      return g is PolylineGeometry && g.closed;
    }
    if (kind == DrawingElementKind.path) {
      final g = geometry;
      return g is PathGeometry && g.hasClose;
    }
    return false;
  }

  factory DrawingElement.parse(Object? value, {String path = 'element'}) {
    final map = _map(value);
    if (map == null) throw DrawingPlanParseException('must be an object', path: path);
    final id = _str(map['id']);
    if (id == null || !_elementIdRe.hasMatch(id) || id.length > 64) {
      throw DrawingPlanParseException('invalid element id', path: '$path.id');
    }
    final kindRaw = _str(map['kind']);
    if (kindRaw == null) {
      throw DrawingPlanParseException('kind is required', path: '$path.kind');
    }
    final kind = DrawingElementKind.parse(kindRaw, path: '$path.kind');
    final geometryRaw = map['geometry'];
    if (geometryRaw == null) {
      throw DrawingPlanParseException('geometry is required', path: '$path.geometry');
    }
    final geometry = _parseGeometry(kind, geometryRaw, path: '$path.geometry');

    ProtocolStyle? style;
    if (map['style'] != null) {
      style = ProtocolStyle.parse(map['style'], path: '$path.style');
    }

    final layer = _clampInt(_int(map['layer']) ?? 0, 0, 5);
    final rawStage = _int(map['stage']) ?? 1;
    if (rawStage < 1 || rawStage > kDrawingMaxStages) {
      throw DrawingPlanParseException('stage must be in [1, $kDrawingMaxStages]', path: '$path.stage');
    }
    final durationMs = _clampInt(_int(map['duration_ms']) ?? 600, kDrawingMinDurationMs, kDrawingMaxDurationMs);
    final delayMs = _clampInt(_int(map['delay_ms']) ?? 0, 0, kDrawingMaxDelayMs);
    final clipId = _str(map['clip_id']);
    if (clipId != null && (!_elementIdRe.hasMatch(clipId) || clipId.length > 64)) {
      throw DrawingPlanParseException('invalid clip_id', path: '$path.clip_id');
    }

    final element = DrawingElement(
      id: id,
      kind: kind,
      geometry: geometry,
      style: style,
      layer: layer,
      stage: rawStage,
      durationMs: durationMs,
      delayMs: delayMs,
      clipId: clipId,
    );

    if (!element.isClosed && style?.fillColor != null) {
      throw DrawingPlanParseException(
        'fill_color is only valid on closed elements (kind=${kind.wireName})',
        path: '$path.style.fill_color',
      );
    }
    return element;
  }

  Map<String, dynamic> toJson() => <String, dynamic>{
        'id': id,
        'kind': kind.wireName,
        'geometry': geometry.toJson(),
        'layer': layer,
        'stage': stage,
        'duration_ms': durationMs,
        'delay_ms': delayMs,
        if (style != null && style!.toJson().isNotEmpty) 'style': style!.toJson(),
        if (clipId != null) 'clip_id': clipId,
      };

  BoardRect bounds() => geometry.bounds();
}

class DrawingStage {
  const DrawingStage({
    required this.stage,
    required this.description,
    this.elementIds = const <String>[],
    this.pauseMs = 0,
  });

  final int stage;
  final String description;
  final List<String> elementIds;
  final int pauseMs;

  factory DrawingStage.parse(Object? value, {String path = 'stage'}) {
    final map = _map(value);
    if (map == null) throw DrawingPlanParseException('must be an object', path: path);
    final stage = _int(map['stage']);
    if (stage == null || stage < 1 || stage > kDrawingMaxStages) {
      throw DrawingPlanParseException('stage must be in [1, $kDrawingMaxStages]', path: '$path.stage');
    }
    final description = _str(map['description']) ?? '';
    if (description.length > 240) {
      throw DrawingPlanParseException('description too long', path: '$path.description');
    }
    final rawIds = map['element_ids'];
    final elementIds = <String>[];
    if (rawIds != null) {
      final list = _list(rawIds);
      if (list == null) {
        throw DrawingPlanParseException('element_ids must be a list', path: '$path.element_ids');
      }
      for (var i = 0; i < list.length; i++) {
        final id = _str(list[i]);
        if (id == null) {
          throw DrawingPlanParseException('element_ids must contain strings', path: '$path.element_ids[$i]');
        }
        elementIds.add(id);
      }
    }
    final pauseMs = _clampInt(_int(map['pause_ms']) ?? 0, 0, kDrawingMaxPauseMs);
    return DrawingStage(
      stage: stage,
      description: description,
      elementIds: List<String>.unmodifiable(elementIds),
      pauseMs: pauseMs,
    );
  }

  Map<String, dynamic> toJson() => <String, dynamic>{
        'stage': stage,
        'description': description,
        'pause_ms': pauseMs,
        if (elementIds.isNotEmpty) 'element_ids': elementIds.toList(),
      };
}

class DrawingPlan {
  const DrawingPlan({
    required this.protocol,
    required this.version,
    required this.elements,
    this.title,
    this.stages = const <DrawingStage>[],
    this.focus,
    this.triggers = const <Map<String, dynamic>>[],
  });

  final String protocol;
  final int version;
  final String? title;
  final List<DrawingElement> elements;
  final List<DrawingStage> stages;
  final Map<String, dynamic>? focus;
  final List<Map<String, dynamic>> triggers;

  static DrawingPlan parse(Object? value) {
    final map = _map(value);
    if (map == null) {
      throw DrawingPlanParseException('drawing plan must be a JSON object');
    }
    final protocol = _str(map['protocol']) ?? kDrawingProtocolName;
    if (protocol != kDrawingProtocolName) {
      throw DrawingPlanParseException('protocol must be "$kDrawingProtocolName"', path: 'protocol');
    }
    final version = _int(map['version']) ?? kDrawingProtocolVersion;
    if (version != kDrawingProtocolVersion) {
      throw DrawingPlanParseException('version must be $kDrawingProtocolVersion', path: 'version');
    }
    final titleRaw = _str(map['title']);
    if (titleRaw != null && titleRaw.length > 200) {
      throw DrawingPlanParseException('title too long', path: 'title');
    }

    final rawElements = _list(map['elements']);
    if (rawElements == null || rawElements.isEmpty || rawElements.length > kDrawingMaxElements) {
      throw DrawingPlanParseException('elements must have 1..$kDrawingMaxElements items', path: 'elements');
    }
    final elements = <DrawingElement>[];
    for (var i = 0; i < rawElements.length; i++) {
      elements.add(DrawingElement.parse(rawElements[i], path: 'elements[$i]'));
    }

    final rawStages = _list(map['stages']) ?? const <dynamic>[];
    if (rawStages.length > kDrawingMaxStages) {
      throw DrawingPlanParseException('stages must have <= $kDrawingMaxStages items', path: 'stages');
    }
    final stages = <DrawingStage>[];
    for (var i = 0; i < rawStages.length; i++) {
      stages.add(DrawingStage.parse(rawStages[i], path: 'stages[$i]'));
    }

    // Validate references
    final ids = elements.map((e) => e.id).toList();
    final idSet = ids.toSet();
    if (idSet.length != ids.length) {
      throw DrawingPlanParseException('element ids must be unique', path: 'elements');
    }
    final position = <String, int>{};
    for (var i = 0; i < ids.length; i++) {
      position[ids[i]] = i;
    }
    for (final element in elements) {
      if (element.clipId != null) {
        final targetIndex = position[element.clipId!];
        if (targetIndex == null || targetIndex >= position[element.id]!) {
          throw DrawingPlanParseException(
            'clip_id "${element.clipId}" must reference an earlier element',
            path: 'elements.${element.id}.clip_id',
          );
        }
        if (!elements[targetIndex].isClosed) {
          throw DrawingPlanParseException(
            'clip_id "${element.clipId}" must reference a closed element',
            path: 'elements.${element.id}.clip_id',
          );
        }
      }
    }
    final declaredStages = stages.map((s) => s.stage).toSet();
    if (stages.isNotEmpty) {
      for (final element in elements) {
        if (!declaredStages.contains(element.stage)) {
          throw DrawingPlanParseException(
            'element "${element.id}" references undeclared stage ${element.stage}',
            path: 'elements.${element.id}.stage',
          );
        }
      }
    }
    for (final stage in stages) {
      for (final elementId in stage.elementIds) {
        if (!idSet.contains(elementId)) {
          throw DrawingPlanParseException(
            'stage ${stage.stage} references unknown element id "$elementId"',
            path: 'stages.${stage.stage}.element_ids',
          );
        }
      }
    }
    if (!elements.any((e) => e.kind != DrawingElementKind.text)) {
      throw DrawingPlanParseException('drawing plan must contain at least one drawable element');
    }

    Map<String, dynamic>? focus;
    final rawFocus = map['focus'];
    if (rawFocus is Map) focus = _map(rawFocus);
    final triggers = <Map<String, dynamic>>[];
    final rawTriggers = map['triggers'];
    if (rawTriggers is List) {
      for (final item in rawTriggers) {
        final m = _map(item);
        if (m != null) triggers.add(m);
      }
    }

    return DrawingPlan(
      protocol: protocol,
      version: version,
      title: titleRaw,
      elements: List<DrawingElement>.unmodifiable(elements),
      stages: List<DrawingStage>.unmodifiable(stages),
      focus: focus == null ? null : Map<String, dynamic>.unmodifiable(focus),
      triggers: List<Map<String, dynamic>>.unmodifiable(triggers),
    );
  }

  static DrawingPlan? tryParse(Object? value) {
    try {
      return parse(value);
    } catch (_) {
      return null;
    }
  }

  Map<String, dynamic> toJson() => <String, dynamic>{
        'protocol': protocol,
        'version': version,
        if (title != null) 'title': title,
        'elements': elements.map((e) => e.toJson()).toList(),
        if (stages.isNotEmpty) 'stages': stages.map((s) => s.toJson()).toList(),
        if (focus != null) 'focus': focus,
        if (triggers.isNotEmpty) 'triggers': triggers,
      };

  int get totalDurationMs => computeTotalDurationMs(this);
  List<BoardRect> get elementBounds => elements.map((e) => e.bounds()).toList(growable: false);
}

DrawingGeometry _parseGeometry(DrawingElementKind kind, Object? raw, {String path = 'geometry'}) {
  switch (kind) {
    case DrawingElementKind.line:
      return LineGeometry.parse(raw, path: path);
    case DrawingElementKind.polyline:
      return PolylineGeometry.parse(raw, path: path);
    case DrawingElementKind.polygon:
      final g = PolylineGeometry.parse(raw, path: path);
      return PolylineGeometry(points: g.points, closed: true, smooth: g.smooth);
    case DrawingElementKind.rectangle:
      return RectangleGeometry.parse(raw, path: path);
    case DrawingElementKind.ellipse:
      return EllipseGeometry.parse(raw, path: path);
    case DrawingElementKind.arc:
      return ArcGeometry.parse(raw, path: path);
    case DrawingElementKind.quadBezier:
      return QuadBezierGeometry.parse(raw, path: path);
    case DrawingElementKind.cubicBezier:
      return CubicBezierGeometry.parse(raw, path: path);
    case DrawingElementKind.path:
      return PathGeometry.parse(raw, path: path);
    case DrawingElementKind.text:
      return TextGeometry.parse(raw, path: path);
    case DrawingElementKind.arrow:
      return ArrowGeometry.parse(raw, path: path);
  }
}

BoardRect _boundsFromPoints(Iterable<BoardPoint> points) {
  final list = points.toList(growable: false);
  if (list.isEmpty) return BoardRect.zero;
  var minX = list.first.x;
  var minY = list.first.y;
  var maxX = list.first.x;
  var maxY = list.first.y;
  for (final p in list) {
    if (p.x < minX) minX = p.x;
    if (p.y < minY) minY = p.y;
    if (p.x > maxX) maxX = p.x;
    if (p.y > maxY) maxY = p.y;
  }
  return BoardRect(minX, minY, maxX - minX, maxY - minY);
}

/// Computes total sequential reveal duration (ms) for a plan.
/// Mirrors backend `plan_total_duration_ms`.
int computeTotalDurationMs(DrawingPlan plan) {
  if (plan.elements.isEmpty) return 0;
  final byId = <String, DrawingElement>{
    for (final e in plan.elements) e.id: e,
  };
  final order = plan.elements.toList(growable: false);

  List<int> stageNumbers;
  if (plan.stages.isNotEmpty) {
    final set = <int>{};
    for (final s in plan.stages) set.add(s.stage);
    stageNumbers = set.toList()..sort();
  } else {
    stageNumbers = <int>[1];
  }

  var elapsed = 0;
  final scheduled = <String>{};
  for (var i = 0; i < stageNumbers.length; i++) {
    final stageNumber = stageNumbers[i];
    var members = order.where((e) => e.stage == stageNumber).toList(growable: false);
    var pauseMs = 0;
    if (plan.stages.isNotEmpty) {
      for (final stage in plan.stages) {
        if (stage.stage == stageNumber) {
          if (stage.elementIds.isNotEmpty) {
            final explicit = <DrawingElement>[];
            for (final id in stage.elementIds) {
              final e = byId[id];
              if (e != null) explicit.add(e);
            }
            final remaining = members.where((e) => !stage.elementIds.contains(e.id)).toList();
            members = <DrawingElement>[...explicit, ...remaining];
          }
          pauseMs = stage.pauseMs;
          break;
        }
      }
    }
    for (final element in members) {
      if (scheduled.contains(element.id)) continue;
      scheduled.add(element.id);
      final start = elapsed + element.delayMs;
      elapsed = start + element.durationMs;
    }
    if (i != stageNumbers.length - 1) {
      elapsed += pauseMs;
    }
  }
  // Elements outside declared stages draw after them.
  for (final element in order) {
    if (scheduled.contains(element.id)) continue;
    scheduled.add(element.id);
    elapsed += element.durationMs;
  }
  return elapsed;
}

/// Resolves per-element timeline windows (startMs, endMs) within the overall plan.
/// Used by the renderer to map global progress to element progress.
class DrawingElementTimeline {
  const DrawingElementTimeline({
    required this.elementId,
    required this.startMs,
    required this.endMs,
  });

  final String elementId;
  final int startMs;
  final int endMs;
  int get durationMs => endMs - startMs;
}

List<DrawingElementTimeline> computeElementTimelines(DrawingPlan plan) {
  if (plan.elements.isEmpty) return const <DrawingElementTimeline>[];
  final byId = <String, DrawingElement>{for (final e in plan.elements) e.id: e};
  final order = plan.elements.toList(growable: false);

  List<int> stageNumbers;
  if (plan.stages.isNotEmpty) {
    final set = <int>{};
    for (final s in plan.stages) set.add(s.stage);
    stageNumbers = set.toList()..sort();
  } else {
    stageNumbers = <int>[1];
  }

  final timelines = <DrawingElementTimeline>[];
  var elapsed = 0;
  final scheduled = <String>{};

  void schedule(DrawingElement element) {
    if (scheduled.contains(element.id)) return;
    scheduled.add(element.id);
    final start = elapsed + element.delayMs;
    final end = start + element.durationMs;
    timelines.add(DrawingElementTimeline(elementId: element.id, startMs: start, endMs: end));
    elapsed = end;
  }

  for (var i = 0; i < stageNumbers.length; i++) {
    final stageNumber = stageNumbers[i];
    var members = order.where((e) => e.stage == stageNumber).toList(growable: false);
    var pauseMs = 0;
    if (plan.stages.isNotEmpty) {
      for (final stage in plan.stages) {
        if (stage.stage == stageNumber) {
          if (stage.elementIds.isNotEmpty) {
            final explicit = <DrawingElement>[];
            for (final id in stage.elementIds) {
              final e = byId[id];
              if (e != null) explicit.add(e);
            }
            final remaining = members.where((e) => !stage.elementIds.contains(e.id)).toList();
            members = <DrawingElement>[...explicit, ...remaining];
          }
          pauseMs = stage.pauseMs;
          break;
        }
      }
    }
    for (final element in members) {
      schedule(element);
    }
    if (i != stageNumbers.length - 1) {
      elapsed += pauseMs;
    }
  }
  for (final element in order) {
    schedule(element);
  }
  return timelines;
}


