import 'board_geometry.dart';

/// The 22 board event types from CONTRACTS §3 plus an `unknown` sentinel so a
/// malformed/forward-compatible stream never crashes the client.
enum BoardEventType {
  writeText('write_text'),
  writeHeading('write_heading'),
  writeDefinition('write_definition'),
  writeBulletList('write_bullet_list'),
  updateText('update_text'),
  writeEquation('write_equation'),
  updateEquation('update_equation'),
  highlightEquation('highlight_equation'),
  drawLine('draw_line'),
  drawShape('draw_shape'),
  drawPath('draw_path'),
  drawArrow('draw_arrow'),
  drawDiagram('draw_diagram'),
  createCoordinateSystem('create_coordinate_system'),
  plotFunction('plot_function'),
  plotPoints('plot_points'),
  addGraphLabel('add_graph_label'),
  erase('erase'),
  eraseElement('erase_element'),
  highlight('highlight'),
  clear('clear'),
  moveElement('move_element'),
  unknown('unknown');

  const BoardEventType(this.wireName);

  /// The snake_case wire name from CONTRACTS §3.
  final String wireName;

  bool get isKnown => this != BoardEventType.unknown;

  /// Element layer derived from the event type (CONTRACTS §2: never sent
  /// redundantly by the backend). Management events return 0.
  int get layer {
    switch (this) {
      case BoardEventType.writeText:
      case BoardEventType.writeHeading:
      case BoardEventType.writeDefinition:
      case BoardEventType.writeBulletList:
      case BoardEventType.updateText:
        return 2;
      case BoardEventType.writeEquation:
      case BoardEventType.updateEquation:
        return 3;
      case BoardEventType.drawLine:
      case BoardEventType.drawShape:
      case BoardEventType.drawPath:
      case BoardEventType.drawArrow:
      case BoardEventType.drawDiagram:
        return 4;
      case BoardEventType.createCoordinateSystem:
      case BoardEventType.plotFunction:
      case BoardEventType.plotPoints:
      case BoardEventType.addGraphLabel:
        return 5;
      case BoardEventType.highlight:
      case BoardEventType.highlightEquation:
        return 6;
      default:
        return 0;
    }
  }

  /// True for event types that create/upsert a board element.
  bool get createsElement => layer > 0;

  /// True for text-ish events rendered by the chalk text layer.
  bool get isTextual =>
      this == BoardEventType.writeText ||
      this == BoardEventType.writeHeading ||
      this == BoardEventType.writeDefinition ||
      this == BoardEventType.writeBulletList ||
      this == BoardEventType.updateText;

  static BoardEventType fromWire(Object? wire) {
    final name = wire?.toString().trim();
    if (name == null || name.isEmpty) return BoardEventType.unknown;
    for (final type in BoardEventType.values) {
      if (type.wireName == name) return type;
    }
    return BoardEventType.unknown;
  }
}

/// Base class for the per-type payload DTOs (CONTRACTS §3). All parsing is
/// tolerant: malformed input records a warning and never throws.
abstract class BoardPayload {
  const BoardPayload(this.raw);

  /// The untouched wire payload (may contain extra/forward-compatible fields).
  final Map<String, dynamic> raw;

  /// False when a required field for this event type is missing/unparseable.
  bool get isValid => true;

  /// Human readable parse warnings (non-fatal contract drift).
  List<String> get warnings => const <String>[];

  BoardPoint? get position => null;
  BoardSize? get size => null;
  String? get color => null;
  String? get style => null;
}

/// Typed payload for `write_text` / `update_text`.
class WriteTextPayload extends BoardPayload {
  WriteTextPayload(super.raw)
      : text = _str(raw['text']),
        style = _str(raw['style']),
        position = BoardPoint.tryParse(raw['position']),
        size = BoardSize.tryParse(raw['size']),
        color = _str(raw['color']),
        align = _str(raw['align']),
        fontScale = asDouble(raw['font_scale']),
        maxWidth = asDouble(raw['max_width']),
        _warnings = <String>[
          if (_str(raw['text']) == null) 'write_text.text is required',
        ];

  final String? text;
  @override
  final String? style;
  @override
  final BoardPoint? position;
  @override
  final BoardSize? size;
  @override
  final String? color;
  final String? align;
  final double? fontScale;
  final double? maxWidth;

  final List<String> _warnings;

  @override
  bool get isValid => text != null && text!.isNotEmpty;

  @override
  List<String> get warnings => List<String>.unmodifiable(_warnings);
}

/// Typed payload for `write_heading`.
class WriteHeadingPayload extends BoardPayload {
  WriteHeadingPayload(super.raw)
      : text = _str(raw['text']),
        position = BoardPoint.tryParse(raw['position']),
        level = (asDouble(raw['level']) ?? 1).round().clamp(1, 2),
        color = _str(raw['color']),
        underline = raw['underline'] == true,
        _warnings = <String>[
          if (_str(raw['text']) == null) 'write_heading.text is required',
        ];

  final String? text;
  @override
  final BoardPoint? position;
  final int level;
  @override
  final String? color;
  final bool underline;

  final List<String> _warnings;

  @override
  bool get isValid => text != null && text!.isNotEmpty;

  @override
  List<String> get warnings => List<String>.unmodifiable(_warnings);

  @override
  String? get style => level == 1 ? 'heading' : 'subheading';
}

/// Typed payload for `write_definition`.
class WriteDefinitionPayload extends BoardPayload {
  WriteDefinitionPayload(super.raw)
      : term = _str(raw['term']),
        text = _str(raw['text']),
        position = BoardPoint.tryParse(raw['position']),
        color = _str(raw['color']),
        termStyle = _str(raw['term_style']),
        _warnings = <String>[
          if (_str(raw['term']) == null) 'write_definition.term is required',
          if (_str(raw['text']) == null) 'write_definition.text is required',
        ];

  final String? term;
  final String? text;
  @override
  final BoardPoint? position;
  @override
  final String? color;
  final String? termStyle;

  final List<String> _warnings;

  @override
  bool get isValid => term != null && text != null;

  @override
  List<String> get warnings => List<String>.unmodifiable(_warnings);

  @override
  String? get style => 'definition';
}

/// Typed payload for `write_bullet_list`.
class WriteBulletListPayload extends BoardPayload {
  WriteBulletListPayload(super.raw)
      : items = _stringList(raw['items']),
        position = BoardPoint.tryParse(raw['position']),
        style = _str(raw['style']) ?? 'bullet',
        color = _str(raw['color']),
        markerColor = _str(raw['marker_color']),
        _warnings = <String>[
          if (_stringList(raw['items']).isEmpty)
            'write_bullet_list.items must be a non-empty list',
        ];

  final List<String> items;
  @override
  final BoardPoint? position;
  @override
  final String? style;
  @override
  final String? color;
  final String? markerColor;

  final List<String> _warnings;

  bool get isNumbered => style == 'numbered';

  @override
  bool get isValid => items.isNotEmpty;

  @override
  List<String> get warnings => List<String>.unmodifiable(_warnings);
}

/// Typed payload for `write_equation`.
class EquationPayload extends BoardPayload {
  EquationPayload(super.raw)
      : latex = _str(raw['latex']),
        position = BoardPoint.tryParse(raw['position']),
        size = BoardSize.tryParse(raw['size']),
        color = _str(raw['color']),
        fontScale = asDouble(raw['font_scale']),
        caption = _str(raw['caption']),
        _warnings = <String>[
          if (_str(raw['latex']) == null) 'write_equation.latex is required',
        ];

  final String? latex;
  @override
  final BoardPoint? position;
  @override
  final BoardSize? size;
  @override
  final String? color;
  final double? fontScale;
  final String? caption;

  final List<String> _warnings;

  @override
  bool get isValid => latex != null && latex!.trim().isNotEmpty;

  @override
  List<String> get warnings => List<String>.unmodifiable(_warnings);
}

/// Typed payload for `update_equation`.
class UpdateEquationPayload extends BoardPayload {
  UpdateEquationPayload(super.raw)
      : latex = _str(raw['latex']),
        color = _str(raw['color']),
        fontScale = asDouble(raw['font_scale']);

  final String? latex;
  @override
  final String? color;
  final double? fontScale;
}

/// Typed payload for `highlight_equation`.
class HighlightEquationPayload extends BoardPayload {
  HighlightEquationPayload(super.raw)
      : color = _str(raw['color']),
        style = _str(raw['style']) ?? 'box';

  @override
  final String? color;
  @override
  final String? style;
}

/// Typed payload for `draw_line`.
class DrawLinePayload extends BoardPayload {
  DrawLinePayload(super.raw)
      : start = BoardPoint.tryParse(raw['start']),
        end = BoardPoint.tryParse(raw['end']),
        color = _str(raw['color']),
        strokeWidth = asDouble(raw['stroke_width']),
        dash = _doubleList(raw['dash']),
        arrowhead = raw['arrowhead'] == true,
        _warnings = <String>[
          if (BoardPoint.tryParse(raw['start']) == null)
            'draw_line.start is required',
          if (BoardPoint.tryParse(raw['end']) == null)
            'draw_line.end is required',
        ];

  final BoardPoint? start;
  final BoardPoint? end;
  @override
  final String? color;
  final double? strokeWidth;
  final List<double>? dash;
  final bool arrowhead;

  final List<String> _warnings;

  @override
  bool get isValid => start != null && end != null;

  @override
  List<String> get warnings => List<String>.unmodifiable(_warnings);
}

/// Typed payload for `draw_shape`.
class DrawShapePayload extends BoardPayload {
  DrawShapePayload(super.raw)
      : shape = _str(raw['shape']),
        position = BoardPoint.tryParse(raw['position']),
        size = BoardSize.tryParse(raw['size']),
        color = _str(raw['color']),
        fillColor = _str(raw['fill_color']),
        strokeWidth = asDouble(raw['stroke_width']),
        corners = asDouble(raw['corners']),
        points = _points(raw['points']),
        _warnings = <String>[
          if (_str(raw['shape']) == null) 'draw_shape.shape is required',
        ];

  static const Set<String> shapes = <String>{
    'rect',
    'circle',
    'ellipse',
    'triangle',
    'polygon',
    'rhombus',
    'cylinder',
  };

  final String? shape;
  @override
  final BoardPoint? position;
  @override
  final BoardSize? size;
  @override
  final String? color;
  final String? fillColor;
  final double? strokeWidth;
  final double? corners;
  final List<BoardPoint>? points;

  final List<String> _warnings;

  @override
  bool get isValid => shape != null;

  @override
  List<String> get warnings => List<String>.unmodifiable(_warnings);
}

/// Typed payload for `draw_path`.
class DrawPathPayload extends BoardPayload {
  DrawPathPayload(super.raw)
      : points = _points(raw['points']) ?? const <BoardPoint>[],
        closed = raw['closed'] == true,
        smooth = raw['smooth'] != false,
        color = _str(raw['color']),
        strokeWidth = asDouble(raw['stroke_width']),
        fillColor = _str(raw['fill_color']),
        _warnings = <String>[
          if (_points(raw['points']) == null ||
              (_points(raw['points'])?.length ?? 0) < 2)
            'draw_path.points must contain at least 2 points',
        ];

  final List<BoardPoint> points;
  final bool closed;
  final bool smooth;
  @override
  final String? color;
  final double? strokeWidth;
  final String? fillColor;

  final List<String> _warnings;

  @override
  bool get isValid => points.length >= 2;

  @override
  List<String> get warnings => List<String>.unmodifiable(_warnings);
}

/// Typed payload for `draw_arrow`.
class DrawArrowPayload extends BoardPayload {
  DrawArrowPayload(super.raw)
      : start = BoardPoint.tryParse(raw['start']),
        end = BoardPoint.tryParse(raw['end']),
        curve = asDouble(raw['curve']) ?? 0,
        label = _str(raw['label']),
        color = _str(raw['color']),
        strokeWidth = asDouble(raw['stroke_width']),
        _warnings = <String>[
          if (BoardPoint.tryParse(raw['start']) == null)
            'draw_arrow.start is required',
          if (BoardPoint.tryParse(raw['end']) == null)
            'draw_arrow.end is required',
        ];

  final BoardPoint? start;
  final BoardPoint? end;
  final double curve;
  final String? label;
  @override
  final String? color;
  final double? strokeWidth;

  final List<String> _warnings;

  @override
  bool get isValid => start != null && end != null;

  @override
  List<String> get warnings => List<String>.unmodifiable(_warnings);
}

/// A node of a `draw_diagram` payload.
class DiagramNode {
  const DiagramNode({
    required this.id,
    this.label,
    this.shape,
    required this.position,
    this.size,
    this.color,
  });

  final String id;
  final String? label;
  final String? shape;
  final BoardPoint position;
  final BoardSize? size;
  final String? color;

  static DiagramNode? tryParse(Object? json) {
    if (json is! Map) return null;
    final id = _str(json['id']);
    final position = BoardPoint.tryParse(json['position']);
    if (id == null || position == null) return null;
    return DiagramNode(
      id: id,
      label: _str(json['label']),
      shape: _str(json['shape']),
      position: position,
      size: BoardSize.tryParse(json['size']),
      color: _str(json['color']),
    );
  }

  Map<String, dynamic> toJson() => <String, dynamic>{
        'id': id,
        if (label != null) 'label': label,
        if (shape != null) 'shape': shape,
        'position': position.toJson(),
        if (size != null) 'size': size!.toJson(),
        if (color != null) 'color': color,
      };
}

/// An edge of a `draw_diagram` payload.
class DiagramEdge {
  const DiagramEdge({
    required this.from,
    required this.to,
    this.label,
    this.style,
    this.arrow,
    this.curve,
  });

  final String from;
  final String to;
  final String? label;
  final String? style;
  final bool? arrow;
  final double? curve;

  static DiagramEdge? tryParse(Object? json) {
    if (json is! Map) return null;
    final from = _str(json['from']);
    final to = _str(json['to']);
    if (from == null || to == null) return null;
    return DiagramEdge(
      from: from,
      to: to,
      label: _str(json['label']),
      style: _str(json['style']),
      arrow: json['arrow'] is bool ? json['arrow'] as bool : null,
      curve: asDouble(json['curve']),
    );
  }

  Map<String, dynamic> toJson() => <String, dynamic>{
        'from': from,
        'to': to,
        if (label != null) 'label': label,
        if (style != null) 'style': style,
        if (arrow != null) 'arrow': arrow,
        if (curve != null) 'curve': curve,
      };
}

/// Typed payload for `draw_diagram`.
class DrawDiagramPayload extends BoardPayload {
  DrawDiagramPayload(super.raw)
      : kind = _str(raw['kind']) ?? 'generic',
        nodes = _nodes(raw['nodes']),
        edges = _edges(raw['edges']),
        title = _str(raw['title']),
        coordinateNote = _str(raw['coordinate_note']);

  static const Set<String> kinds = <String>{
    'flowchart',
    'circuit',
    'force',
    'biology',
    'concept_map',
    'geometry',
    'generic',
  };

  final String kind;
  final List<DiagramNode> nodes;
  final List<DiagramEdge> edges;
  final String? title;
  final String? coordinateNote;

  @override
  bool get isValid => nodes.isNotEmpty;

  @override
  List<String> get warnings => nodes.isEmpty
      ? const <String>['draw_diagram.nodes must contain at least one node']
      : const <String>[];

  @override
  BoardPoint? get position =>
      nodes.isEmpty ? null : nodes.first.position;

  @override
  BoardSize? get size => null;

  DiagramNode? nodeById(String id) {
    for (final node in nodes) {
      if (node.id == id) return node;
    }
    return null;
  }
}

/// Typed payload for `create_coordinate_system`.
class CoordinateSystemPayload extends BoardPayload {
  CoordinateSystemPayload(super.raw)
      : position = BoardPoint.tryParse(raw['position']),
        size = BoardSize.tryParse(raw['size']),
        xRange = _range(raw['x_range']),
        yRange = _range(raw['y_range']),
        grid = raw['grid'] == true,
        xLabel = _str(raw['x_label']),
        yLabel = _str(raw['y_label']),
        xStep = asDouble(raw['x_step']),
        yStep = asDouble(raw['y_step']),
        color = _str(raw['color']),
        _warnings = <String>[
          if (BoardPoint.tryParse(raw['position']) == null)
            'create_coordinate_system.position is required',
          if (_range(raw['x_range']) == null)
            'create_coordinate_system.x_range is required',
          if (_range(raw['y_range']) == null)
            'create_coordinate_system.y_range is required',
        ];

  @override
  final BoardPoint? position;
  @override
  final BoardSize? size;
  final List<double>? xRange;
  final List<double>? yRange;
  final bool grid;
  final String? xLabel;
  final String? yLabel;
  final double? xStep;
  final double? yStep;
  @override
  final String? color;

  final List<String> _warnings;

  @override
  bool get isValid => xRange != null && yRange != null && position != null;

  @override
  List<String> get warnings => List<String>.unmodifiable(_warnings);
}

/// Typed payload for `plot_function`. `samples` are data-space coordinates,
/// ordered by x; the backend samples the expression.
class PlotFunctionPayload extends BoardPayload {
  PlotFunctionPayload(super.raw)
      : coordinateSystemId = _str(raw['coordinate_system_id']),
        expression = _str(raw['expression']),
        xDomain = _range(raw['x_domain']),
        samples = _points(raw['samples']) ?? const <BoardPoint>[],
        color = _str(raw['color']),
        strokeWidth = asDouble(raw['stroke_width']),
        style = _str(raw['style']) ?? 'solid',
        label = _str(raw['label']);

  final String? coordinateSystemId;
  final String? expression;
  final List<double>? xDomain;
  final List<BoardPoint> samples;
  @override
  final String? color;
  final double? strokeWidth;
  @override
  final String? style;
  final String? label;

  @override
  bool get isValid => samples.length >= 2;

  @override
  List<String> get warnings => isValid
      ? const <String>[]
      : const <String>[
          'plot_function.samples was absent or has fewer than 2 points; '
              'nothing will be plotted',
        ];
}

/// A point of a `plot_points` payload (data coordinates).
class PlotPointData {
  const PlotPointData({
    required this.x,
    required this.y,
    this.label,
    this.color,
    this.radius,
  });

  final double x;
  final double y;
  final String? label;
  final String? color;
  final double? radius;

  static PlotPointData? tryParse(Object? json) {
    if (json is! Map) return null;
    final x = asDouble(json['x']);
    final y = asDouble(json['y']);
    if (x == null || y == null) return null;
    return PlotPointData(
      x: x,
      y: y,
      label: _str(json['label']),
      color: _str(json['color']),
      radius: asDouble(json['radius']),
    );
  }

  Map<String, dynamic> toJson() => <String, dynamic>{
        'x': x,
        'y': y,
        if (label != null) 'label': label,
        if (color != null) 'color': color,
        if (radius != null) 'radius': radius,
      };
}

/// Typed payload for `plot_points`.
class PlotPointsPayload extends BoardPayload {
  PlotPointsPayload(super.raw)
      : coordinateSystemId = _str(raw['coordinate_system_id']),
        points = _plotPoints(raw['points']),
        color = _str(raw['color']),
        _warnings = <String>[
          if (_plotPoints(raw['points']).isEmpty)
            'plot_points.points must contain at least one point',
        ];

  final String? coordinateSystemId;
  final List<PlotPointData> points;
  @override
  final String? color;

  final List<String> _warnings;

  @override
  bool get isValid => points.isNotEmpty;

  @override
  List<String> get warnings => List<String>.unmodifiable(_warnings);
}

/// Typed payload for `add_graph_label`.
class GraphLabelPayload extends BoardPayload {
  GraphLabelPayload(super.raw)
      : coordinateSystemId = _str(raw['coordinate_system_id']),
        at = BoardPoint.tryParse(raw['at']),
        text = _str(raw['text']),
        color = _str(raw['color']),
        withLeader = raw['with_leader'] != false,
        _warnings = <String>[
          if (_str(raw['text']) == null) 'add_graph_label.text is required',
          if (BoardPoint.tryParse(raw['at']) == null)
            'add_graph_label.at is required',
        ];

  final String? coordinateSystemId;
  final BoardPoint? at;
  final String? text;
  @override
  final String? color;
  final bool withLeader;

  final List<String> _warnings;

  @override
  bool get isValid => text != null && at != null;

  @override
  List<String> get warnings => List<String>.unmodifiable(_warnings);
}

/// Typed payload for `erase`.
class ErasePayload extends BoardPayload {
  ErasePayload(super.raw)
      : scope = _str(raw['scope']) ?? 'all',
        region = BoardRect.tryParse(raw['region']),
        _warnings = <String>[
          if ((_str(raw['scope']) ?? 'all') == 'region' &&
              BoardRect.tryParse(raw['region']) == null)
            'erase.region is required when scope == region',
        ];

  final String scope;
  final BoardRect? region;

  final List<String> _warnings;

  @override
  bool get isValid => scope != 'region' || region != null;

  @override
  List<String> get warnings => List<String>.unmodifiable(_warnings);
}

/// Typed payload for `highlight` (layer 6 annotation referencing live bounds).
class HighlightPayload extends BoardPayload {
  HighlightPayload(super.raw)
      : targetIds = _stringList(raw['target_ids']),
        style = _str(raw['style']) ?? 'box',
        color = _str(raw['color']),
        label = _str(raw['label']),
        _warnings = <String>[
          if (_stringList(raw['target_ids']).isEmpty)
            'highlight.target_ids must contain at least one id',
        ];

  static const Set<String> styles = <String>{
    'underline',
    'circle',
    'box',
    'strike',
    'arrow',
  };

  final List<String> targetIds;
  @override
  final String? style;
  @override
  final String? color;
  final String? label;

  final List<String> _warnings;

  @override
  bool get isValid => targetIds.isNotEmpty;

  @override
  List<String> get warnings => List<String>.unmodifiable(_warnings);
}

/// Typed payload for `move_element`.
class MoveElementPayload extends BoardPayload {
  MoveElementPayload(super.raw)
      : position = BoardPoint.tryParse(raw['position']),
        delta = BoardPoint.tryParse(raw['delta']),
        _warnings = <String>[
          if (raw['position'] == null && raw['delta'] == null)
            'move_element requires position or delta',
        ];

  @override
  final BoardPoint? position;
  final BoardPoint? delta;

  final List<String> _warnings;

  @override
  bool get isValid => position != null || delta != null;

  @override
  List<String> get warnings => List<String>.unmodifiable(_warnings);
}

/// Payload for management events with no fields (`clear`, `erase_element`).
class EmptyPayload extends BoardPayload {
  const EmptyPayload(super.raw);
}

/// Opaque payload for an unknown/forward-compatible event type.
class UnknownPayload extends BoardPayload {
  const UnknownPayload(super.raw);

  @override
  bool get isValid => false;
}

/// Envelope for one board operation (CONTRACTS §2) with the typed payload
/// parsed per CONTRACTS §3. Parsing never throws: bad input degrades to
/// warnings and a `BoardEventType.unknown`/invalid payload.
class BoardEvent {
  BoardEvent({
    required this.eventId,
    required this.sessionId,
    required this.type,
    required this.sequence,
    required this.boardRevision,
    required this.elementId,
    required this.payload,
    required this.dependsOn,
    required this.animate,
    required this.createdAt,
    this.source,
    BoardEventType? kind,
    BoardPayload? parsedPayload,
    List<String> warnings = const <String>[],
  })  : kind = kind ?? BoardEventType.fromWire(type),
        payloadParsed = parsedPayload ?? _parsePayload(kind, payload),
        envelopeWarnings = List<String>.unmodifiable(warnings);

  /// Tolerant parse of the §2 envelope + §3 payload. Never throws.
  factory BoardEvent.fromJson(Map<String, dynamic> json) {
    final warnings = <String>[];
    final type = _str(json['type']) ?? '';
    final sequence = asDouble(json['sequence'])?.round() ?? 0;
    final boardRevision =
        asDouble(json['board_revision'])?.round() ?? sequence;
    final eventId = _str(json['event_id']) ??
        'synthetic_${sequence}_${type.isEmpty ? 'unknown' : type}';
    if (_str(json['event_id']) == null) {
      warnings.add('event_id missing; synthesized $eventId');
    }
    if (sequence <= 0) warnings.add('sequence missing or invalid');
    final rawPayload = _map(json['payload']) ?? <String, dynamic>{};
    final payload = Map<String, dynamic>.unmodifiable(rawPayload);
    final kind = BoardEventType.fromWire(type);
    if (!kind.isKnown) warnings.add('unknown board event type "$type"');
    final createdAtRaw = _str(json['created_at']);
    final createdAt =
        DateTime.tryParse(createdAtRaw ?? '')?.toUtc() ??
            DateTime.fromMillisecondsSinceEpoch(0, isUtc: true);
    return BoardEvent(
      eventId: eventId,
      sessionId: _str(json['session_id']) ?? '',
      type: type,
      sequence: sequence,
      boardRevision: boardRevision < sequence ? sequence : boardRevision,
      elementId: _str(json['element_id']),
      payload: payload,
      dependsOn: _stringList(json['depends_on']),
      animate: json['animate'] is bool ? json['animate'] as bool : true,
      createdAt: createdAt,
      source: _str(json['source']),
      kind: kind,
      parsedPayload: _parsePayload(kind, payload),
      warnings: warnings,
    );
  }

  final String eventId;
  final String sessionId;

  /// The wire type string (one of CONTRACTS §3 or an unknown value).
  final String type;
  final int sequence;
  final int boardRevision;
  final String? elementId;
  final Map<String, dynamic> payload;
  final List<String> dependsOn;
  final bool animate;
  final DateTime createdAt;
  final String? source;

  /// Parsed enum form of [type].
  final BoardEventType kind;

  /// Typed per-type payload DTO (CONTRACTS §3).
  final BoardPayload payloadParsed;

  /// Envelope-level parse warnings captured by [BoardEvent.fromJson].
  final List<String> envelopeWarnings;

  List<String>? _combinedWarnings;

  /// Non-fatal parse warnings (contract drift, missing optional fields, ...).
  List<String> get warnings => _combinedWarnings ??= () {
        final payloadWarnings = payloadParsed.warnings;
        if (envelopeWarnings.isEmpty) return payloadWarnings;
        if (payloadWarnings.isEmpty) return envelopeWarnings;
        return List<String>.unmodifiable(
          <String>[...envelopeWarnings, ...payloadWarnings],
        );
      }();

  /// True when the envelope has the minimum fields needed to apply safely.
  bool get isWellFormed => sequence > 0 && type.isNotEmpty;

  bool get isKnownType => kind.isKnown;

  bool get createsElement => kind.createsElement;

  int get layer => kind.layer;

  // ---------------------------------------------------------------------------
  // Convenience typed getters (never throw; null when not applicable).
  // ---------------------------------------------------------------------------
  BoardPoint? get position => payloadParsed.position;
  BoardSize? get size => payloadParsed.size;
  String? get text {
    final data = payloadParsed;
    if (data is WriteTextPayload) return data.text;
    if (data is WriteHeadingPayload) return data.text;
    if (data is WriteDefinitionPayload) return data.text;
    if (data is GraphLabelPayload) return data.text;
    return null;
  }

  String? get style => payloadParsed.style;
  String? get color => payloadParsed.color;
  double get fontScale {
    final data = payloadParsed;
    if (data is WriteTextPayload) return data.fontScale ?? 1;
    if (data is EquationPayload) return data.fontScale ?? 1;
    if (data is UpdateEquationPayload) return data.fontScale ?? 1;
    return 1;
  }

  String? get latex {
    final data = payloadParsed;
    if (data is EquationPayload) return data.latex;
    if (data is UpdateEquationPayload) return data.latex;
    return null;
  }

  List<String> get targetIds {
    final data = payloadParsed;
    if (data is HighlightPayload) return data.targetIds;
    final id = elementId;
    if (id != null &&
        (kind == BoardEventType.highlightEquation)) {
      return <String>[id];
    }
    return const <String>[];
  }

  String? get coordinateSystemId {
    final data = payloadParsed;
    if (data is PlotFunctionPayload) return data.coordinateSystemId;
    if (data is PlotPointsPayload) return data.coordinateSystemId;
    if (data is GraphLabelPayload) return data.coordinateSystemId;
    return null;
  }

  List<BoardPoint> get samples {
    final data = payloadParsed;
    return data is PlotFunctionPayload ? data.samples : const <BoardPoint>[];
  }

  Map<String, dynamic> toJson() => <String, dynamic>{
        'event_id': eventId,
        'session_id': sessionId,
        'sequence': sequence,
        'type': type,
        'element_id': elementId,
        'payload': payload,
        'created_at': createdAt.toUtc().toIso8601String(),
        'board_revision': boardRevision,
        'depends_on': dependsOn,
        if (source != null) 'source': source,
        'animate': animate,
      };

  @override
  String toString() =>
      'BoardEvent($sequence $type ${elementId ?? '-'} rev=$boardRevision)';

  static BoardPayload _parsePayload(
    BoardEventType? kind,
    Map<String, dynamic> raw,
  ) {
    switch (kind) {
      case BoardEventType.writeText:
      case BoardEventType.updateText:
        return WriteTextPayload(raw);
      case BoardEventType.writeHeading:
        return WriteHeadingPayload(raw);
      case BoardEventType.writeDefinition:
        return WriteDefinitionPayload(raw);
      case BoardEventType.writeBulletList:
        return WriteBulletListPayload(raw);
      case BoardEventType.writeEquation:
        return EquationPayload(raw);
      case BoardEventType.updateEquation:
        return UpdateEquationPayload(raw);
      case BoardEventType.highlightEquation:
        return HighlightEquationPayload(raw);
      case BoardEventType.drawLine:
        return DrawLinePayload(raw);
      case BoardEventType.drawShape:
        return DrawShapePayload(raw);
      case BoardEventType.drawPath:
        return DrawPathPayload(raw);
      case BoardEventType.drawArrow:
        return DrawArrowPayload(raw);
      case BoardEventType.drawDiagram:
        return DrawDiagramPayload(raw);
      case BoardEventType.createCoordinateSystem:
        return CoordinateSystemPayload(raw);
      case BoardEventType.plotFunction:
        return PlotFunctionPayload(raw);
      case BoardEventType.plotPoints:
        return PlotPointsPayload(raw);
      case BoardEventType.addGraphLabel:
        return GraphLabelPayload(raw);
      case BoardEventType.erase:
        return ErasePayload(raw);
      case BoardEventType.eraseElement:
        return EmptyPayload(raw);
      case BoardEventType.highlight:
        return HighlightPayload(raw);
      case BoardEventType.clear:
        return EmptyPayload(raw);
      case BoardEventType.moveElement:
        return MoveElementPayload(raw);
      case BoardEventType.unknown:
      case null:
        return UnknownPayload(raw);
    }
  }
}

// -----------------------------------------------------------------------------
// Tolerant JSON coercion helpers (shared by BoardEvent/BoardElement parsers).
// -----------------------------------------------------------------------------
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

List<String> _stringList(Object? value) {
  if (value is List) {
    return value
        .where((dynamic item) => item != null)
        .map((dynamic item) => item.toString())
        .toList(growable: false);
  }
  return const <String>[];
}

List<double>? _doubleList(Object? value) {
  if (value is! List) return null;
  final result = <double>[];
  for (final item in value) {
    final number = asDouble(item);
    if (number == null || !number.isFinite) return null;
    result.add(number);
  }
  return result;
}

List<BoardPoint>? _points(Object? value) {
  if (value is! List) return null;
  final result = <BoardPoint>[];
  for (final item in value) {
    final point = BoardPoint.tryParse(item);
    if (point == null) continue;
    result.add(point);
  }
  return result;
}

List<double>? _range(Object? value) {
  if (value is! List || value.length < 2) return null;
  final min = asDouble(value[0]);
  final max = asDouble(value[1]);
  if (min == null || max == null || !min.isFinite || !max.isFinite) {
    return null;
  }
  return min <= max ? <double>[min, max] : <double>[max, min];
}

List<DiagramNode> _nodes(Object? value) {
  if (value is! List) return const <DiagramNode>[];
  final nodes = <DiagramNode>[];
  for (final item in value) {
    final node = DiagramNode.tryParse(item);
    if (node != null) nodes.add(node);
  }
  return List<DiagramNode>.unmodifiable(nodes);
}

List<DiagramEdge> _edges(Object? value) {
  if (value is! List) return const <DiagramEdge>[];
  final edges = <DiagramEdge>[];
  for (final item in value) {
    final edge = DiagramEdge.tryParse(item);
    if (edge != null) edges.add(edge);
  }
  return List<DiagramEdge>.unmodifiable(edges);
}

List<PlotPointData> _plotPoints(Object? value) {
  if (value is! List) return const <PlotPointData>[];
  final points = <PlotPointData>[];
  for (final item in value) {
    final point = PlotPointData.tryParse(item);
    if (point != null) points.add(point);
  }
  return List<PlotPointData>.unmodifiable(points);
}

/// The canonical ordered list of board event wire types (CONTRACTS §3),
/// exported for the contract-parity test against docs/contracts/event_types.json.
const List<String> kBoardEventWireTypes = <String>[
  'write_text',
  'write_heading',
  'write_definition',
  'write_bullet_list',
  'update_text',
  'write_equation',
  'update_equation',
  'highlight_equation',
  'draw_line',
  'draw_shape',
  'draw_path',
  'draw_arrow',
  'draw_diagram',
  'create_coordinate_system',
  'plot_function',
  'plot_points',
  'add_graph_label',
  'erase',
  'erase_element',
  'highlight',
  'clear',
  'move_element',
];
