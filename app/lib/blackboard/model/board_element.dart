import 'board_event.dart';
import 'board_geometry.dart';

/// Immutable board element DTO (CONTRACTS §5).
///
/// `payload` is the original per-type payload (CONTRACTS §3). `level` is
/// derived from the type (CONTRACTS §2) but validated on the way in. Deletion is
/// soft: [removedAtSequence] is set instead of dropping the record, so replay is
/// stable.
class BoardElement {
  const BoardElement({
    required this.elementId,
    required this.type,
    required this.level,
    required this.payload,
    this.position,
    this.size,
    this.style,
    this.color,
    required this.createdSequence,
    this.updatedSequence,
    this.removedAtSequence,
    this.animation = const <String, dynamic>{},
  });

  /// Tolerant parse of a §5 `BoardElement`. Never throws; unknown types map to
  /// level 6 with a warning through [BoardElement.fromEvent] callers.
  factory BoardElement.fromJson(Map<String, dynamic> json) {
    final rawPayload = _map(json['payload']) ?? <String, dynamic>{};
    final type = _str(json['type']) ?? '';
    final kind = BoardEventType.fromWire(type);
    final rawLevel = asDouble(json['level'])?.round() ?? kind.layer;
    final position = BoardPoint.tryParse(json['position']);
    final size = BoardSize.tryParse(json['size']);
    return BoardElement(
      elementId: _str(json['element_id']) ?? '',
      type: type,
      level: rawLevel.clamp(1, 6),
      payload: Map<String, dynamic>.unmodifiable(rawPayload),
      position: position,
      size: size,
      style: _str(json['style']),
      color: _str(json['color']),
      createdSequence: asDouble(json['created_sequence'])?.round() ?? 0,
      updatedSequence: asDouble(json['updated_sequence'])?.round(),
      removedAtSequence: asDouble(json['removed_at_sequence'])?.round(),
      animation: Map<String, dynamic>.unmodifiable(
        _map(json['animation']) ?? const <String, dynamic>{},
      ),
    );
  }

  final String elementId;
  final String type;
  final int level;
  final Map<String, dynamic> payload;
  final BoardPoint? position;
  final BoardSize? size;
  final String? style;
  final String? color;
  final int createdSequence;
  final int? updatedSequence;
  final int? removedAtSequence;

  /// Optional animation metadata from the backend (e.g. `duration_ms`).
  final Map<String, dynamic> animation;

  BoardEventType get kind => BoardEventType.fromWire(type);

  bool get isVisible => removedAtSequence == null;

  bool get isTextual => kind.isTextual;

  bool get isEquation =>
      type == BoardEventType.writeEquation.wireName ||
      type == BoardEventType.updateEquation.wireName;

  bool get isDrawing => level == 4;

  bool get isGraph => level == 5;

  bool get isAnnotation =>
      type == BoardEventType.highlight.wireName ||
      type == BoardEventType.highlightEquation.wireName;

  bool get isCoordinateSystem =>
      type == BoardEventType.createCoordinateSystem.wireName;

  bool get isPlotFunction => type == BoardEventType.plotFunction.wireName;

  bool get isPlotPoints => type == BoardEventType.plotPoints.wireName;

  bool get isGraphLabel => type == BoardEventType.addGraphLabel.wireName;

  /// Normalized bounds from the declared position/size when both are present.
  /// Callers that need a reliable box for annotations should use
  /// `BoardDocument.boundsOf` or the renderer's measured bounds registry.
  BoardRect? get declaredBounds {
    final pos = position;
    final sz = size;
    if (pos == null) return null;
    if (sz == null) return null;
    return BoardRect.fromPositionSize(pos, sz);
  }

  /// Requested reveal duration from backend animation metadata, if any.
  int? get requestedDurationMs {
    final direct = asDouble(animation['duration_ms']);
    if (direct != null && direct > 0) return direct.round().clamp(80, 10000);
    final seconds = asDouble(animation['duration_s']);
    if (seconds != null && seconds > 0) {
      return (seconds * 1000).round().clamp(80, 10000);
    }
    return null;
  }

  BoardElement copyWith({
    String? elementId,
    String? type,
    int? level,
    Map<String, dynamic>? payload,
    BoardPoint? position,
    bool clearPosition = false,
    BoardSize? size,
    bool clearSize = false,
    String? style,
    bool clearStyle = false,
    String? color,
    bool clearColor = false,
    int? createdSequence,
    int? updatedSequence,
    bool clearUpdatedSequence = false,
    int? removedAtSequence,
    bool clearRemovedAtSequence = false,
    Map<String, dynamic>? animation,
  }) {
    return BoardElement(
      elementId: elementId ?? this.elementId,
      type: type ?? this.type,
      level: level ?? this.level,
      payload: payload ?? this.payload,
      position: clearPosition ? null : (position ?? this.position),
      size: clearSize ? null : (size ?? this.size),
      style: clearStyle ? null : (style ?? this.style),
      color: clearColor ? null : (color ?? this.color),
      createdSequence: createdSequence ?? this.createdSequence,
      updatedSequence: clearUpdatedSequence
          ? null
          : (updatedSequence ?? this.updatedSequence),
      removedAtSequence: clearRemovedAtSequence
          ? null
          : (removedAtSequence ?? this.removedAtSequence),
      animation: animation ?? this.animation,
    );
  }

  /// Builds an element from a creating event per CONTRACTS §3.
  static BoardElement? fromEvent(BoardEvent event, {required int sequence}) {
    if (!event.createsElement) return null;
    final id = event.elementId;
    if (id == null || id.trim().isEmpty) return null;
    if (!event.payloadParsed.isValid) return null;
    final payload = Map<String, dynamic>.unmodifiable(event.payload);
    final position = event.position;
    final size = event.size;
    return BoardElement(
      elementId: id,
      type: event.type,
      level: event.layer == 0 ? 2 : event.layer,
      payload: payload,
      position: position,
      size: size,
      style: event.style,
      color: event.color,
      createdSequence: sequence,
    );
  }

  Map<String, dynamic> toJson() => <String, dynamic>{
        'element_id': elementId,
        'type': type,
        'level': level,
        'payload': payload,
        if (position != null) 'position': position!.toJson(),
        if (size != null) 'size': size!.toJson(),
        if (style != null) 'style': style,
        if (color != null) 'color': color,
        'created_sequence': createdSequence,
        if (updatedSequence != null) 'updated_sequence': updatedSequence,
        if (removedAtSequence != null)
          'removed_at_sequence': removedAtSequence,
        if (animation.isNotEmpty) 'animation': animation,
      };

  @override
  String toString() =>
      'BoardElement($elementId $type l=$level seq=$createdSequence'
      '${removedAtSequence != null ? ' removed@$removedAtSequence' : ''})';
}

String? _str(Object? value) {
  if (value == null) return null;
  if (value is String) return value;
  return value.toString();
}

Map<String, dynamic>? _map(Object? value) {
  if (value is Map<String, dynamic>) return value;
  if (value is Map) {
    return value.map<String, dynamic>(
      (dynamic key, dynamic val) => MapEntry<String, dynamic>(key.toString(), val),
    );
  }
  return null;
}
