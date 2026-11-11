import 'dart:math' as math;
import 'dart:ui' show Offset, Rect, Size;

/// A point in normalized board coordinates (0..1, origin top-left, y down).
class BoardPoint {
  const BoardPoint(this.x, this.y);

  final double x;
  final double y;

  static const BoardPoint zero = BoardPoint(0, 0);

  bool get isFinite => x.isFinite && y.isFinite;

  BoardPoint clamp01() => BoardPoint(x.clamp(0.0, 1.0), y.clamp(0.0, 1.0));

  BoardPoint operator +(BoardPoint other) => BoardPoint(x + other.x, y + other.y);
  BoardPoint operator -(BoardPoint other) => BoardPoint(x - other.x, y - other.y);
  BoardPoint operator *(double factor) => BoardPoint(x * factor, y * factor);

  Offset toOffset() => Offset(x, y);

  /// Tolerant parse: accepts numbers or numeric strings, never throws.
  static BoardPoint? tryParse(Object? json) {
    if (json is BoardPoint) return json;
    if (json is Map) {
      final x = _asDouble(json['x']);
      final y = _asDouble(json['y']);
      if (x != null && y != null && x.isFinite && y.isFinite) {
        return BoardPoint(x, y);
      }
    }
    if (json is List && json.length >= 2) {
      final x = _asDouble(json[0]);
      final y = _asDouble(json[1]);
      if (x != null && y != null && x.isFinite && y.isFinite) {
        return BoardPoint(x, y);
      }
    }
    return null;
  }

  Map<String, dynamic> toJson() => <String, dynamic>{'x': x, 'y': y};

  @override
  String toString() => 'BoardPoint($x, $y)';

  @override
  bool operator ==(Object other) =>
      other is BoardPoint && other.x == x && other.y == y;

  @override
  int get hashCode => Object.hash(x, y);
}

/// A size in normalized board units.
class BoardSize {
  const BoardSize(this.w, this.h);

  final double w;
  final double h;

  bool get isFinite => w.isFinite && h.isFinite && w >= 0 && h >= 0;

  Size toSize() => Size(w, h);

  static BoardSize? tryParse(Object? json) {
    if (json is BoardSize) return json;
    if (json is Map) {
      final w = _asDouble(json['w'] ?? json['width']);
      final h = _asDouble(json['h'] ?? json['height']);
      if (w != null && h != null && w.isFinite && h.isFinite) {
        return BoardSize(w, h);
      }
    }
    if (json is List && json.length >= 2) {
      final w = _asDouble(json[0]);
      final h = _asDouble(json[1]);
      if (w != null && h != null && w.isFinite && h.isFinite) {
        return BoardSize(w, h);
      }
    }
    return null;
  }

  Map<String, dynamic> toJson() => <String, dynamic>{'w': w, 'h': h};

  @override
  String toString() => 'BoardSize($w, $h)';

  @override
  bool operator ==(Object other) =>
      other is BoardSize && other.w == w && other.h == h;

  @override
  int get hashCode => Object.hash(w, h);
}

/// An axis-aligned rectangle in normalized board coordinates.
class BoardRect {
  const BoardRect(this.x, this.y, this.w, this.h);

  factory BoardRect.fromLTWH(double x, double y, double w, double h) =>
      BoardRect(x, y, w, h);

  factory BoardRect.fromPoints(BoardPoint a, BoardPoint b) {
    final minX = math.min(a.x, b.x);
    final minY = math.min(a.y, b.y);
    return BoardRect(
      minX,
      minY,
      math.max(a.x, b.x) - minX,
      math.max(a.y, b.y) - minY,
    );
  }

  factory BoardRect.fromPositionSize(BoardPoint position, BoardSize size) =>
      BoardRect(position.x, position.y, size.w, size.h);

  final double x;
  final double y;
  final double w;
  final double h;

  static const BoardRect zero = BoardRect(0, 0, 0, 0);

  double get right => x + w;
  double get bottom => y + h;
  double get centerX => x + w / 2;
  double get centerY => y + h / 2;
  BoardPoint get center => BoardPoint(centerX, centerY);
  BoardPoint get topLeft => BoardPoint(x, y);
  BoardPoint get bottomRight => BoardPoint(right, bottom);
  bool get isEmpty => w <= 0 || h <= 0;

  Rect toRect() => Rect.fromLTWH(x, y, w, h);

  BoardRect inflate(double amount) =>
      BoardRect(x - amount, y - amount, w + amount * 2, h + amount * 2);

  BoardRect clamp01() {
    final left = x.clamp(0.0, 1.0);
    final top = y.clamp(0.0, 1.0);
    final r = right.clamp(0.0, 1.0);
    final b = bottom.clamp(0.0, 1.0);
    return BoardRect(left, top, math.max(0.0, r - left), math.max(0.0, b - top));
  }

  BoardRect union(BoardRect other) {
    final left = math.min(x, other.x);
    final top = math.min(y, other.y);
    final r = math.max(right, other.right);
    final b = math.max(bottom, other.bottom);
    return BoardRect(left, top, r - left, b - top);
  }

  bool intersects(BoardRect other) {
    if (isEmpty || other.isEmpty) return false;
    return x < other.right &&
        other.x < right &&
        y < other.bottom &&
        other.y < bottom;
  }

  bool containsPoint(BoardPoint point) =>
      point.x >= x && point.x <= right && point.y >= y && point.y <= bottom;

  static BoardRect? tryParse(Object? json) {
    if (json is BoardRect) return json;
    if (json is Map) {
      final x = _asDouble(json['x']);
      final y = _asDouble(json['y']);
      final w = _asDouble(json['w'] ?? json['width']);
      final h = _asDouble(json['h'] ?? json['height']);
      if (x != null && y != null && w != null && h != null) {
        return BoardRect(x, y, w, h);
      }
    }
    return null;
  }

  Map<String, dynamic> toJson() =>
      <String, dynamic>{'x': x, 'y': y, 'w': w, 'h': h};

  @override
  String toString() => 'BoardRect($x, $y, $w, $h)';

  @override
  bool operator ==(Object other) =>
      other is BoardRect &&
      other.x == x &&
      other.y == y &&
      other.w == w &&
      other.h == h;

  @override
  int get hashCode => Object.hash(x, y, w, h);
}

/// Tolerant numeric coercion shared by the tolerant JSON parsers in this module.
double? asDouble(Object? value) => _asDouble(value);

double? _asDouble(Object? value) {
  if (value is num) return value.toDouble();
  if (value is String) return double.tryParse(value.trim());
  return null;
}
