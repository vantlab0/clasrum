"""Pydantic v2 argument models + OpenAI-style JSON Schemas for the tool registry.

Authority: ``docs/CONTRACTS.md`` §3 (board event payloads) and §4 (tool registry).

Design rules enforced here (all validation happens server-side, once):

* Every tool argument object is *closed* (``extra="forbid"`` -> JSON Schema
  ``additionalProperties: false``) and every field is bounded/typed.
* Numeric fields reject ``NaN``/``inf`` (``allow_inf_nan=False``) and carry
  explicit ``minimum``/``maximum`` in the emitted schema.
* Text is length-limited (<= 2000 chars), list lengths are capped, and colors
  are either a chalk palette role name or an explicit ``#RRGGBB`` string.
* :meth:`BoardArgsModel.build_payload` returns the *normalized* board-event
  payload: fully resolved defaults, rounded floats, no ``None`` values. The
  registry never passes raw model-derived dicts through.
* :func:`openai_tool_schema` produces the OpenAI function-tool schema sent to
  Qwen (``$ref`` inlined, ``title``/``default`` noise stripped, closed objects).

This module contains **no** I/O, no mutation and no side effects: it is pure
validation/normalisation. Board mutation lives in :mod:`app.board.service`.
"""
from __future__ import annotations

import math
import re
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.db.constants import CHALK_COLORS

# ---------------------------------------------------------------------------
# primitive building blocks
# ---------------------------------------------------------------------------

MAX_TEXT = 2000
MAX_SHORT_TEXT = 300
MAX_LABEL = 200
MAX_LATEX = 1200
MAX_ID = 64
MAX_EXPRESSION = 200
MAX_TITLE = 300
MAX_CAPTION = 300
MAX_NOTES = 500
MAX_ITEMS = 20
MAX_POINTS = 400
MAX_DIAGRAM_NODES = 20
MAX_DIAGRAM_EDGES = 40
MAX_DATA_POINTS = 50
MAX_DASH_SEGMENTS = 20

# Data-coordinate magnitude used by graph tools (not normalized canvas space).
DATA_COORD_LIMIT = 1_000_000.0

HEX_OR_ROLE_PATTERN = (
    r"^(#(?:[0-9a-fA-F]{6})|primary|secondary|blue|yellow|red|green)$"
)
_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _normalize_color(value: str) -> str:
    value = value.strip()
    if value in CHALK_COLORS:
        return value
    if _HEX_RE.match(value):
        return "#" + value[1:].upper()
    raise ValueError(
        "color must be a chalk role name (primary, secondary, blue, yellow, red, "
        "green) or a #RRGGBB hex string"
    )


ColorStr = Annotated[
    str,
    StringConstraints(pattern=HEX_OR_ROLE_PATTERN),
    AfterValidator(_normalize_color),
]

Normalized = Annotated[
    float,
    Field(ge=0.0, le=1.0, allow_inf_nan=False),
]
PositiveNormalized = Annotated[
    float,
    Field(gt=0.0, le=1.0, allow_inf_nan=False),
]
StrokeWidth = Annotated[float, Field(ge=0.5, le=4.0, allow_inf_nan=False)]
FontScale = Annotated[float, Field(ge=0.5, le=2.5, allow_inf_nan=False)]
Curve = Annotated[float, Field(ge=-1.0, le=1.0, allow_inf_nan=False)]
DataCoord = Annotated[
    float, Field(ge=-DATA_COORD_LIMIT, le=DATA_COORD_LIMIT, allow_inf_nan=False)
]
Text2000 = Annotated[str, Field(min_length=1, max_length=MAX_TEXT)]
ShortText = Annotated[str, Field(min_length=1, max_length=MAX_SHORT_TEXT)]
LabelText = Annotated[str, Field(min_length=1, max_length=MAX_LABEL)]
IdText = Annotated[str, Field(min_length=1, max_length=MAX_ID)]

TextStyle = Literal[
    "body", "heading", "subheading", "definition", "label", "caption", "note", "code"
]
BulletStyle = Literal["bullet", "numbered"]
ShapeKind = Literal[
    "rect", "circle", "ellipse", "triangle", "polygon", "rhombus", "cylinder"
]
DiagramKind = Literal[
    "flowchart", "circuit", "force", "biology", "concept_map", "geometry", "generic"
]
HighlightStyle = Literal["underline", "circle", "box", "strike", "arrow"]
EquationHighlightStyle = Literal["box", "underline"]
LineStyle = Literal["solid", "dashed"]


def _r(value: float) -> float:
    """Round normalized floats to 4 decimals so stored payloads stay compact."""
    rounded = round(float(value), 4)
    return 0.0 if rounded == 0 else rounded


def _clean(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop ``None`` values (optional fields are omitted, never null)."""
    return {key: value for key, value in payload.items() if value is not None}


class StrictModel(BaseModel):
    """Closed, finite-number argument model base class."""

    model_config = ConfigDict(
        extra="forbid",
        allow_inf_nan=False,
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
    )


# ---------------------------------------------------------------------------
# geometry helpers
# ---------------------------------------------------------------------------


class Position(StrictModel):
    """Normalized top-left corner of an element's bounding box."""

    x: Normalized = Field(description="Normalized x in [0,1], origin at left edge.")
    y: Normalized = Field(description="Normalized y in [0,1], origin at top edge.")

    def to_payload(self) -> dict[str, float]:
        return {"x": _r(self.x), "y": _r(self.y)}


class Size(StrictModel):
    """Normalized element width/height (fractions of the board content rect)."""

    w: PositiveNormalized = Field(description="Normalized width in (0,1].")
    h: PositiveNormalized = Field(description="Normalized height in (0,1].")

    def to_payload(self) -> dict[str, float]:
        return {"w": _r(self.w), "h": _r(self.h)}


class Delta(StrictModel):
    """Normalized translation, each component in [-1,1]."""

    dx: Curve = Field(description="Horizontal delta in [-1,1].")
    dy: Curve = Field(description="Vertical delta in [-1,1].")

    def to_payload(self) -> dict[str, float]:
        return {"dx": _r(self.dx), "dy": _r(self.dy)}


class Region(StrictModel):
    """Normalized rectangular region (used by ``erase`` with scope=region)."""

    x: Normalized = Field(description="Region left edge, 0..1.")
    y: Normalized = Field(description="Region top edge, 0..1.")
    w: PositiveNormalized = Field(description="Region width, (0,1].")
    h: PositiveNormalized = Field(description="Region height, (0,1].")

    @model_validator(mode="after")
    def _inside_board(self) -> "Region":
        if self.x + self.w > 1.0 + 1e-9 or self.y + self.h > 1.0 + 1e-9:
            raise ValueError("region must lie inside the board (x+w <= 1, y+h <= 1)")
        return self

    def to_payload(self) -> dict[str, float]:
        return {"x": _r(self.x), "y": _r(self.y), "w": _r(self.w), "h": _r(self.h)}


class DataRange(StrictModel):
    """Two-element ``[min, max]`` range in data coordinates."""


RangeTuple = Annotated[
    list[DataCoord],
    Field(min_length=2, max_length=2, description="[min, max] with min < max."),
]


def _validate_range(name: str, values: list[float]) -> list[float]:
    if len(values) != 2:
        raise ValueError(f"{name} must have exactly two values [min, max]")
    low, high = float(values[0]), float(values[1])
    if not (math.isfinite(low) and math.isfinite(high)):
        raise ValueError(f"{name} values must be finite")
    if low >= high:
        raise ValueError(f"{name} requires min < max")
    if high - low < 1e-6:
        raise ValueError(f"{name} span is too small")
    return [low, high]


class DataPoint(StrictModel):
    """A point in data coordinates (graph layer), optionally labelled."""

    x: DataCoord = Field(description="Data-space x coordinate.")
    y: DataCoord = Field(description="Data-space y coordinate.")
    label: LabelText | None = Field(default=None, description="Optional point label.")
    color: ColorStr | None = Field(default=None, description="Chalk role or hex.")
    radius: Annotated[float, Field(ge=0.001, le=0.1, allow_inf_nan=False)] | None = Field(
        default=None, description="Marker radius as a fraction of the board (0.001..0.1)."
    )

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"x": _r(self.x), "y": _r(self.y)}
        if self.label is not None:
            payload["label"] = self.label
        if self.color is not None:
            payload["color"] = self.color
        if self.radius is not None:
            payload["radius"] = _r(self.radius)
        return payload


class CanvasPoint(StrictModel):
    """A point in normalized canvas coordinates."""

    x: Normalized
    y: Normalized

    def to_payload(self) -> dict[str, float]:
        return {"x": _r(self.x), "y": _r(self.y)}


# ---------------------------------------------------------------------------
# board tool arguments: text (layer 2)
# ---------------------------------------------------------------------------


class WriteTextArgs(StrictModel):
    """``board_write_text`` -> ``write_text`` (layer 2)."""

    text: Text2000 = Field(description="Text to write on the board (<=2000 chars).")
    style: TextStyle = Field(
        default="body",
        description="Text style; use heading/subheading for structure, code for code.",
    )
    position: Position = Field(description="Top-left position, normalized 0..1.")
    size: Size | None = Field(default=None, description="Optional explicit box size.")
    color: ColorStr | None = Field(default=None, description="Chalk role or hex color.")
    align: Literal["left", "center", "right"] | None = Field(
        default=None, description="Horizontal alignment inside the box."
    )
    font_scale: FontScale | None = Field(
        default=None, description="Relative font scale (0.5..2.5)."
    )
    max_width: PositiveNormalized | None = Field(
        default=None, description="Optional wrap width as a fraction of the board."
    )

    def build_payload(self) -> dict[str, Any]:
        return _clean(
            {
                "text": self.text,
                "style": self.style,
                "position": self.position.to_payload(),
                "size": self.size.to_payload() if self.size else None,
                "color": self.color or "primary",
                "align": self.align or "left",
                "font_scale": _r(self.font_scale) if self.font_scale is not None else None,
                "max_width": _r(self.max_width) if self.max_width is not None else None,
            }
        )


class WriteHeadingArgs(StrictModel):
    """``board_write_heading`` -> ``write_heading`` (layer 2)."""

    text: Annotated[str, Field(min_length=1, max_length=400)] = Field(
        description="Heading text (<=400 chars)."
    )
    position: Position
    level: Literal[1, 2] = Field(default=1, description="1 = lesson title, 2 = section.")
    color: ColorStr | None = Field(default=None)
    underline: bool = Field(default=False)

    def build_payload(self) -> dict[str, Any]:
        return _clean(
            {
                "text": self.text,
                "position": self.position.to_payload(),
                "level": self.level,
                "color": self.color or "primary",
                "underline": self.underline,
            }
        )


class WriteDefinitionArgs(StrictModel):
    """``board_write_definition`` -> ``write_definition`` (layer 2)."""

    term: Annotated[str, Field(min_length=1, max_length=400)] = Field(
        description="The concept being defined."
    )
    text: Text2000 = Field(description="Definition body (<=2000 chars).")
    position: Position
    color: ColorStr | None = Field(default=None)
    term_style: Literal["chalk_blue"] = Field(
        default="chalk_blue",
        description="Presentation reserved for defined terms; only chalk_blue is valid.",
    )

    def build_payload(self) -> dict[str, Any]:
        return {
            "term": self.term,
            "text": self.text,
            "position": self.position.to_payload(),
            "color": self.color or "primary",
            "term_style": self.term_style,
        }


class WriteBulletListArgs(StrictModel):
    """``board_write_bullet_list`` -> ``write_bullet_list`` (layer 2)."""

    items: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=MAX_SHORT_TEXT)]],
        Field(min_length=1, max_length=MAX_ITEMS),
    ] = Field(description=f"1..{MAX_ITEMS} bullet items, each <=300 chars.")
    position: Position
    style: BulletStyle = Field(default="bullet")
    color: ColorStr | None = Field(default=None)
    marker_color: ColorStr | None = Field(default=None)

    def build_payload(self) -> dict[str, Any]:
        return _clean(
            {
                "items": list(self.items),
                "position": self.position.to_payload(),
                "style": self.style,
                "color": self.color or "primary",
                "marker_color": self.marker_color,
            }
        )


class UpdateTextArgs(StrictModel):
    """``board_update_text`` -> ``update_text``; requires an existing element id."""

    element_id: IdText = Field(description="Id of an existing text element to replace.")
    text: Text2000 | None = Field(default=None, description="New full text.")
    style: TextStyle | None = Field(default=None)
    color: ColorStr | None = Field(default=None)
    position: Position | None = Field(default=None)

    @model_validator(mode="after")
    def _at_least_one_change(self) -> "UpdateTextArgs":
        if self.text is None and self.style is None and self.color is None and self.position is None:
            raise ValueError("update_text requires at least one of text/style/color/position")
        return self

    def build_payload(self) -> dict[str, Any]:
        return _clean(
            {
                "text": self.text,
                "style": self.style,
                "color": self.color,
                "position": self.position.to_payload() if self.position else None,
            }
        )


# ---------------------------------------------------------------------------
# board tool arguments: equations (layer 3)
# ---------------------------------------------------------------------------


class WriteEquationArgs(StrictModel):
    """``board_write_equation`` -> ``write_equation`` (layer 3)."""

    latex: Annotated[str, Field(min_length=1, max_length=MAX_LATEX)] = Field(
        description="LaTeX source rendered by the client (no markdown fences)."
    )
    position: Position
    size: Size | None = Field(default=None)
    color: ColorStr | None = Field(default=None)
    font_scale: FontScale | None = Field(default=None)
    caption: Annotated[str, Field(min_length=1, max_length=MAX_CAPTION)] | None = Field(
        default=None
    )

    def build_payload(self) -> dict[str, Any]:
        return _clean(
            {
                "latex": self.latex,
                "position": self.position.to_payload(),
                "size": self.size.to_payload() if self.size else None,
                "color": self.color or "primary",
                "font_scale": _r(self.font_scale) if self.font_scale is not None else None,
                "caption": self.caption,
            }
        )


class UpdateEquationArgs(StrictModel):
    """``board_update_equation`` -> ``update_equation``; target must be an equation."""

    element_id: IdText = Field(description="Existing equation element id.")
    latex: Annotated[str, Field(min_length=1, max_length=MAX_LATEX)] = Field(
        description="Replacement LaTeX for the whole equation."
    )
    color: ColorStr | None = Field(default=None)
    font_scale: FontScale | None = Field(default=None)

    def build_payload(self) -> dict[str, Any]:
        return _clean(
            {
                "latex": self.latex,
                "color": self.color,
                "font_scale": _r(self.font_scale) if self.font_scale is not None else None,
            }
        )


class HighlightEquationArgs(StrictModel):
    """``board_highlight_equation`` -> ``highlight_equation``; target must be equation."""

    element_id: IdText = Field(description="Existing equation element id.")
    color: ColorStr | None = Field(default=None)
    style: EquationHighlightStyle = Field(default="box")

    def build_payload(self) -> dict[str, Any]:
        return {"color": self.color or "yellow", "style": self.style}


# ---------------------------------------------------------------------------
# board tool arguments: drawings (layer 4)
# ---------------------------------------------------------------------------


class DrawLineArgs(StrictModel):
    """``board_draw_line`` -> ``draw_line`` (layer 4)."""

    start: CanvasPoint = Field(description="Normalized start point, 0..1.")
    end: CanvasPoint = Field(description="Normalized end point, 0..1.")
    color: ColorStr | None = Field(default=None)
    stroke_width: StrokeWidth = Field(default=2.0)
    dash: Annotated[
        list[Annotated[float, Field(ge=0.5, le=50.0, allow_inf_nan=False)]],
        Field(min_length=1, max_length=MAX_DASH_SEGMENTS),
    ] | None = Field(default=None, description="Dash pattern lengths (logical px).")
    arrowhead: bool = Field(default=False)

    def build_payload(self) -> dict[str, Any]:
        return _clean(
            {
                "start": self.start.to_payload(),
                "end": self.end.to_payload(),
                "color": self.color or "primary",
                "stroke_width": _r(self.stroke_width),
                "dash": [_r(v) for v in self.dash] if self.dash else None,
                "arrowhead": self.arrowhead,
            }
        )


class DrawShapeArgs(StrictModel):
    """``board_draw_shape`` -> ``draw_shape`` (layer 4)."""

    shape: ShapeKind = Field(description="Shape primitive to draw.")
    position: Position = Field(description="Top-left of the shape's bounding box.")
    size: Size = Field(description="Bounding box size.")
    color: ColorStr | None = Field(default=None)
    fill_color: ColorStr | None = Field(default=None)
    stroke_width: StrokeWidth = Field(default=2.0)
    corners: Annotated[float, Field(ge=0.0, le=0.5, allow_inf_nan=False)] | None = Field(
        default=None, description="Corner rounding for rect shapes (0..0.5)."
    )
    points: Annotated[
        list[CanvasPoint], Field(min_length=3, max_length=200)
    ] | None = Field(default=None, description="Vertices, required for shape=polygon.")

    @model_validator(mode="after")
    def _polygon_needs_points(self) -> "DrawShapeArgs":
        if self.shape == "polygon" and not self.points:
            raise ValueError("draw_shape with shape=polygon requires points (3..200)")
        if self.shape != "polygon" and self.points:
            raise ValueError("points are only valid when shape=polygon")
        return self

    def build_payload(self) -> dict[str, Any]:
        return _clean(
            {
                "shape": self.shape,
                "position": self.position.to_payload(),
                "size": self.size.to_payload(),
                "color": self.color or "primary",
                "fill_color": self.fill_color,
                "stroke_width": _r(self.stroke_width),
                "corners": _r(self.corners) if self.corners is not None else None,
                "points": [p.to_payload() for p in self.points] if self.points else None,
            }
        )


class DrawPathArgs(StrictModel):
    """``board_draw_path`` -> ``draw_path`` (layer 4)."""

    points: Annotated[list[CanvasPoint], Field(min_length=2, max_length=MAX_POINTS)] = Field(
        description=f"2..{MAX_POINTS} normalized points describing the path."
    )
    closed: bool = Field(default=False)
    smooth: bool = Field(default=True)
    color: ColorStr | None = Field(default=None)
    stroke_width: StrokeWidth = Field(default=2.0)
    fill_color: ColorStr | None = Field(default=None)

    def build_payload(self) -> dict[str, Any]:
        return _clean(
            {
                "points": [p.to_payload() for p in self.points],
                "closed": self.closed,
                "smooth": self.smooth,
                "color": self.color or "primary",
                "stroke_width": _r(self.stroke_width),
                "fill_color": self.fill_color,
            }
        )


class DrawArrowArgs(StrictModel):
    """``board_draw_arrow`` -> ``draw_arrow`` (layer 4)."""

    start: CanvasPoint
    end: CanvasPoint
    curve: Curve = Field(default=0.0, description="Curvature -1..1 (0 = straight).")
    label: Annotated[str, Field(min_length=1, max_length=MAX_LABEL)] | None = Field(
        default=None
    )
    color: ColorStr | None = Field(default=None)
    stroke_width: StrokeWidth = Field(default=2.0)

    def build_payload(self) -> dict[str, Any]:
        return _clean(
            {
                "start": self.start.to_payload(),
                "end": self.end.to_payload(),
                "curve": _r(self.curve),
                "label": self.label,
                "color": self.color or "primary",
                "stroke_width": _r(self.stroke_width),
            }
        )


class DiagramNode(StrictModel):
    """A node of a ``draw_diagram`` element."""

    id: IdText = Field(description="Stable node id referenced by edges.")
    label: Annotated[str, Field(min_length=1, max_length=MAX_LABEL)] | None = Field(
        default=None
    )
    shape: ShapeKind | None = Field(default="rect")
    position: Position
    size: Size | None = Field(default=None)
    color: ColorStr | None = Field(default=None)

    def to_payload(self) -> dict[str, Any]:
        return _clean(
            {
                "id": self.id,
                "label": self.label,
                "shape": self.shape,
                "position": self.position.to_payload(),
                "size": self.size.to_payload() if self.size else None,
                "color": self.color,
            }
        )


class DiagramEdge(StrictModel):
    """An edge of a ``draw_diagram`` element."""

    source: IdText = Field(alias="from", description="Source node id.")
    target: IdText = Field(alias="to", description="Target node id.")
    label: Annotated[str, Field(min_length=1, max_length=MAX_LABEL)] | None = Field(
        default=None
    )
    style: Literal["solid", "dashed", "arrow"] = Field(default="solid")
    arrow: bool = Field(default=True)
    curve: Curve = Field(default=0.0)

    def to_payload(self) -> dict[str, Any]:
        return _clean(
            {
                "from": self.source,
                "to": self.target,
                "label": self.label,
                "style": self.style,
                "arrow": self.arrow,
                "curve": _r(self.curve),
            }
        )


class DrawDiagramArgs(StrictModel):
    """``board_draw_diagram`` -> ``draw_diagram`` (layer 4)."""

    kind: DiagramKind = Field(description="Semantic diagram family.")
    nodes: Annotated[
        list[DiagramNode], Field(min_length=1, max_length=MAX_DIAGRAM_NODES)
    ] = Field(description=f"1..{MAX_DIAGRAM_NODES} nodes, unique ids, positions 0..1.")
    edges: Annotated[
        list[DiagramEdge], Field(max_length=MAX_DIAGRAM_EDGES)
    ] = Field(default_factory=list, description=f"0..{MAX_DIAGRAM_EDGES} edges.")
    title: Annotated[str, Field(min_length=1, max_length=MAX_TITLE)] | None = Field(
        default=None
    )
    coordinate_note: Annotated[str, Field(min_length=1, max_length=MAX_NOTES)] | None = (
        Field(default=None, description="Optional coordinate-system note for the diagram.")
    )

    @model_validator(mode="after")
    def _edges_reference_nodes(self) -> "DrawDiagramArgs":
        ids = [node.id for node in self.nodes]
        if len(ids) != len(set(ids)):
            raise ValueError("diagram node ids must be unique")
        id_set = set(ids)
        for edge in self.edges:
            if edge.source not in id_set:
                raise ValueError(f"edge.from references unknown node id {edge.source!r}")
            if edge.target not in id_set:
                raise ValueError(f"edge.to references unknown node id {edge.target!r}")
            if edge.source == edge.target:
                raise ValueError("diagram edges must connect two different nodes")
        return self

    def build_payload(self) -> dict[str, Any]:
        return _clean(
            {
                "kind": self.kind,
                "nodes": [node.to_payload() for node in self.nodes],
                "edges": [edge.to_payload() for edge in self.edges],
                "title": self.title,
                "coordinate_note": self.coordinate_note,
            }
        )


# ---------------------------------------------------------------------------
# board tool arguments: graphs (layer 5)
# ---------------------------------------------------------------------------


class CreateCoordinateSystemArgs(StrictModel):
    """``board_create_coordinate_system`` -> ``create_coordinate_system`` (layer 5)."""

    position: Position = Field(description="Top-left of the plotting area, normalized.")
    size: Size = Field(description="Plot area size, normalized.")
    x_range: RangeTuple = Field(description="X axis data range [min, max].")
    y_range: RangeTuple = Field(description="Y axis data range [min, max].")
    grid: bool = Field(default=True)
    x_label: Annotated[str, Field(min_length=1, max_length=MAX_LABEL)] | None = Field(
        default=None
    )
    y_label: Annotated[str, Field(min_length=1, max_length=MAX_LABEL)] | None = Field(
        default=None
    )
    x_step: Annotated[float, Field(gt=0.0, le=DATA_COORD_LIMIT, allow_inf_nan=False)] | None = (
        Field(default=None, description="Grid/axis tick spacing on x (data units).")
    )
    y_step: Annotated[float, Field(gt=0.0, le=DATA_COORD_LIMIT, allow_inf_nan=False)] | None = (
        Field(default=None)
    )
    color: ColorStr | None = Field(default=None)

    @field_validator("x_range", "y_range")
    @classmethod
    def _range_ok(cls, value: list[float], info: Any) -> list[float]:
        return _validate_range(info.field_name, value)

    def build_payload(self) -> dict[str, Any]:
        return _clean(
            {
                "position": self.position.to_payload(),
                "size": self.size.to_payload(),
                "x_range": [_r(self.x_range[0]), _r(self.x_range[1])],
                "y_range": [_r(self.y_range[0]), _r(self.y_range[1])],
                "grid": self.grid,
                "x_label": self.x_label,
                "y_label": self.y_label,
                "x_step": _r(self.x_step) if self.x_step is not None else None,
                "y_step": _r(self.y_step) if self.y_step is not None else None,
                "color": self.color or "primary",
            }
        )


class PlotFunctionArgs(StrictModel):
    """``board_plot_function`` -> ``plot_function`` (layer 5).

    The backend evaluates ``expression`` with the sandboxed sampler in
    :mod:`app.board.sampling` and injects the computed ``samples`` array; the
    model never supplies sample data.
    """

    coordinate_system_id: IdText = Field(
        description="Id returned by board_create_coordinate_system."
    )
    expression: Annotated[str, Field(min_length=1, max_length=MAX_EXPRESSION)] = Field(
        description="Math expression of x, e.g. 'x^2 - 3*x + 1', 'sin(x)', 'exp(-x)'."
    )
    x_domain: RangeTuple | None = Field(
        default=None, description="Optional sampling domain [min, max]; defaults to the axes range."
    )
    color: ColorStr | None = Field(default=None)
    stroke_width: StrokeWidth = Field(default=2.0)
    style: LineStyle = Field(default="solid")
    label: LabelText | None = Field(default=None)

    @field_validator("x_domain")
    @classmethod
    def _domain_ok(cls, value: list[float] | None) -> list[float] | None:
        if value is None:
            return None
        return _validate_range("x_domain", value)


class PlotPointsArgs(StrictModel):
    """``board_plot_points`` -> ``plot_points`` (layer 5)."""

    coordinate_system_id: IdText
    points: Annotated[
        list[DataPoint], Field(min_length=1, max_length=MAX_DATA_POINTS)
    ] = Field(description=f"1..{MAX_DATA_POINTS} points in data coordinates.")
    color: ColorStr | None = Field(default=None)

    def build_payload(self) -> dict[str, Any]:
        ordered = sorted(self.points, key=lambda point: point.x)
        return _clean(
            {
                "coordinate_system_id": self.coordinate_system_id,
                "points": [point.to_payload() for point in ordered],
                "color": self.color or "blue",
            }
        )


class AddGraphLabelArgs(StrictModel):
    """``board_add_graph_label`` -> ``add_graph_label`` (layer 5)."""

    coordinate_system_id: IdText
    at: DataPoint = Field(description="Anchor point in data coordinates.")
    text: Annotated[str, Field(min_length=1, max_length=MAX_TITLE)] = Field(
        description="Label text (<=300 chars)."
    )
    color: ColorStr | None = Field(default=None)
    with_leader: bool = Field(default=False)

    def build_payload(self) -> dict[str, Any]:
        return _clean(
            {
                "coordinate_system_id": self.coordinate_system_id,
                "at": {"x": _r(self.at.x), "y": _r(self.at.y)},
                "text": self.text,
                "color": self.color or "primary",
                "with_leader": self.with_leader,
            }
        )


# ---------------------------------------------------------------------------
# board tool arguments: management / annotations (layer 6)
# ---------------------------------------------------------------------------


class EraseArgs(StrictModel):
    """``board_erase`` -> ``erase``."""

    scope: Literal["all", "region"] = Field(description="Erase everything or one region.")
    region: Region | None = Field(
        default=None, description="Required when scope=region, forbidden otherwise."
    )

    @model_validator(mode="after")
    def _region_matches_scope(self) -> "EraseArgs":
        if self.scope == "region" and self.region is None:
            raise ValueError("erase with scope=region requires region")
        if self.scope == "all" and self.region is not None:
            raise ValueError("region is only valid when scope=region")
        return self

    def build_payload(self) -> dict[str, Any]:
        return _clean(
            {
                "scope": self.scope,
                "region": self.region.to_payload() if self.region else None,
            }
        )


class EraseElementArgs(StrictModel):
    """``board_erase_element`` -> ``erase_element``; target must exist."""

    element_id: IdText = Field(description="Existing element id to remove.")


class HighlightArgs(StrictModel):
    """``board_highlight`` -> ``highlight``; annotation referencing existing ids."""

    target_ids: Annotated[list[IdText], Field(min_length=1, max_length=MAX_ITEMS)] = Field(
        description="1..20 existing element ids to annotate."
    )
    style: HighlightStyle = Field(default="box")
    color: ColorStr | None = Field(default=None)
    label: Annotated[str, Field(min_length=1, max_length=MAX_SHORT_TEXT)] | None = Field(
        default=None
    )

    def build_payload(self) -> dict[str, Any]:
        return _clean(
            {
                "target_ids": list(self.target_ids),
                "style": self.style,
                "color": self.color or "yellow",
                "label": self.label,
            }
        )


class ClearArgs(StrictModel):
    """``board_clear`` -> ``clear`` (wipes the whole document)."""


class MoveElementArgs(StrictModel):
    """``board_move_element`` -> ``move_element``; target must exist."""

    element_id: IdText = Field(description="Existing element id to move.")
    position: Position | None = Field(default=None, description="Absolute new position.")
    delta: Delta | None = Field(default=None, description="Relative translation.")

    @model_validator(mode="after")
    def _has_movement(self) -> "MoveElementArgs":
        if self.position is None and self.delta is None:
            raise ValueError("move_element requires position or delta")
        return self

    def build_payload(self) -> dict[str, Any]:
        return _clean(
            {
                "position": self.position.to_payload() if self.position else None,
                "delta": self.delta.to_payload() if self.delta else None,
            }
        )


# ---------------------------------------------------------------------------
# lesson tool arguments (LangGraph actions, not board events)
# ---------------------------------------------------------------------------


class LessonNextConceptArgs(StrictModel):
    """``lesson_next_concept`` — advance the teaching state to the next concept."""

    target_concept_id: Annotated[str, Field(min_length=1, max_length=240)] | None = Field(
        default=None, description="Optional explicit concept id to jump to."
    )
    reason: Annotated[str, Field(min_length=1, max_length=MAX_SHORT_TEXT)] | None = Field(
        default=None, description="Why the lesson is advancing (shown in feedback)."
    )


class LessonUpdateProgressArgs(StrictModel):
    """``lesson_update_progress`` — record observed progress / understanding."""

    concept_id: Annotated[str, Field(min_length=1, max_length=240)] | None = Field(
        default=None, description="Concept this progress refers to."
    )
    progress: Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)] | None = Field(
        default=None, description="Overall progress 0..1 (server recomputes from concepts)."
    )
    understanding_score: Annotated[
        float, Field(ge=0.0, le=1.0, allow_inf_nan=False)
    ] | None = Field(default=None, description="Observed understanding 0..1.")
    note: Annotated[str, Field(min_length=1, max_length=MAX_NOTES)] | None = Field(
        default=None, description="Short factual observation about the student."
    )


class LessonAskQuestionArgs(StrictModel):
    """``lesson_ask_question`` — ask the student a question (no board mutation)."""

    question: Annotated[str, Field(min_length=1, max_length=1000)] = Field(
        description="The question to ask, in plain language."
    )
    concept: Annotated[str, Field(min_length=1, max_length=240)] | None = Field(
        default=None, description="Concept id or title the question tests."
    )
    expected_answer: Annotated[str, Field(min_length=1, max_length=1000)] | None = Field(
        default=None, description="Model answer used later for evaluation."
    )


class LessonCheckAnswerArgs(StrictModel):
    """``lesson_check_answer`` — evaluate the student's answer to a question."""

    student_answer: Annotated[str, Field(min_length=1, max_length=MAX_TEXT)] = Field(
        description="What the student answered."
    )
    correct: bool = Field(description="Whether the answer is correct.")
    question_id: Annotated[str, Field(min_length=1, max_length=MAX_ID)] | None = Field(
        default=None, description="Id returned by lesson_ask_question when known."
    )
    concept: Annotated[str, Field(min_length=1, max_length=240)] | None = Field(
        default=None, description="Concept id or title being assessed."
    )
    feedback: Annotated[str, Field(min_length=1, max_length=1000)] | None = Field(
        default=None, description="Short, specific feedback for the student."
    )


class LessonRevisitConceptArgs(StrictModel):
    """``lesson_revisit_concept`` — go back to a concept and re-teach it."""

    concept_id: Annotated[str, Field(min_length=1, max_length=240)] = Field(
        description="Concept id or title to revisit."
    )
    reason: Annotated[str, Field(min_length=1, max_length=MAX_SHORT_TEXT)] | None = Field(
        default=None
    )


class LessonFinishArgs(StrictModel):
    """``lesson_finish`` — end the lesson and summarise it."""

    summary: Annotated[str, Field(min_length=1, max_length=MAX_TEXT)] = Field(
        description="2-4 sentence summary of what was covered and how it went."
    )
    reason: Annotated[str, Field(min_length=1, max_length=MAX_SHORT_TEXT)] | None = Field(
        default=None
    )


# ---------------------------------------------------------------------------
# tool specification table
# ---------------------------------------------------------------------------

BoardTargetMode = Literal["new", "existing", "none"]


class ToolSpec(StrictModel):
    """One registry entry: Qwen-facing schema + mapping to a board event type."""

    name: str
    kind: Literal["board", "lesson"]
    event_type: str | None
    target_mode: BoardTargetMode
    args_model: type[BaseModel]
    description: str
    #: For existing-element tools: element types the target may have (empty = any).
    allowed_element_types: tuple[str, ...] = ()

    def openai_schema(self) -> dict[str, Any]:
        return openai_tool_schema(self.name, self.description, self.args_model)


# (name, event_type, target_mode, args_model, description, allowed element types)
_BOARD_SPECS: tuple[tuple[str, str, BoardTargetMode, type[BaseModel], str, tuple[str, ...]], ...] = (
    (
        "board_write_text",
        "write_text",
        "new",
        WriteTextArgs,
        "Write a block of chalk text (body/heading/subheading/label/caption/note/code). "
        "Positions and sizes are normalized to [0,1] of the board, origin top-left. "
        "Use for explanations, steps, code snippets (style='code') and short notes.",
        (),
    ),
    (
        "board_write_heading",
        "write_heading",
        "new",
        WriteHeadingArgs,
        "Write a heading. level=1 for the lesson title, level=2 for section headings. "
        "Keep headings short and place them near the top of the board.",
        (),
    ),
    (
        "board_write_definition",
        "write_definition",
        "new",
        WriteDefinitionArgs,
        "Write a formal definition with a highlighted term. Use once per key concept "
        "after building intuition.",
        (),
    ),
    (
        "board_write_bullet_list",
        "write_bullet_list",
        "new",
        WriteBulletListArgs,
        "Write 1-20 bullet or numbered points. Great for steps, properties, or a "
        "worked-example breakdown.",
        (),
    ),
    (
        "board_update_text",
        "update_text",
        "existing",
        UpdateTextArgs,
        "Replace text/style/color/position of an existing text element (e.g. correct a "
        "mistake). Requires the element_id returned by the original write tool.",
        (
            "write_text",
            "write_heading",
            "write_definition",
            "write_bullet_list",
        ),
    ),
    (
        "board_write_equation",
        "write_equation",
        "new",
        WriteEquationArgs,
        "Write a LaTeX equation. Use standard LaTeX math syntax without $ delimiters. "
        "Place equations after the intuition that motivates them.",
        (),
    ),
    (
        "board_update_equation",
        "update_equation",
        "existing",
        UpdateEquationArgs,
        "Replace the LaTeX of an existing equation element. Requires its element_id.",
        ("write_equation",),
    ),
    (
        "board_highlight_equation",
        "highlight_equation",
        "existing",
        HighlightEquationArgs,
        "Draw attention to an existing equation (box or underline) with a chalk color.",
        ("write_equation",),
    ),
    (
        "board_draw_line",
        "draw_line",
        "new",
        DrawLineArgs,
        "Draw a straight line between two normalized points. Use for axes, separators "
        "and simple sketches.",
        (),
    ),
    (
        "board_draw_shape",
        "draw_shape",
        "new",
        DrawShapeArgs,
        "Draw a shape primitive (rect, circle, ellipse, triangle, polygon, rhombus, "
        "cylinder). For polygon provide 3+ normalized points.",
        (),
    ),
    (
        "board_draw_path",
        "draw_path",
        "new",
        DrawPathArgs,
        "Draw a freehand smooth path from 2+ normalized points. Use for curves, "
        "underline strokes and quick sketches.",
        (),
    ),
    (
        "board_draw_arrow",
        "draw_arrow",
        "new",
        DrawArrowArgs,
        "Draw an arrow between two normalized points, optionally curved and labelled. "
        "Use for flow, force directions and cause-effect.",
        (),
    ),
    (
        "board_draw_diagram",
        "draw_diagram",
        "new",
        DrawDiagramArgs,
        "Draw a labelled node/edge diagram (flowchart, circuit, force, biology, "
        "concept_map, geometry, generic). Node ids are referenced by edges.",
        (),
    ),
    (
        "board_create_coordinate_system",
        "create_coordinate_system",
        "new",
        CreateCoordinateSystemArgs,
        "Create a 2D coordinate system / plot area. Returns element_id; pass it as "
        "coordinate_system_id to the plot tools. Choose ranges that show the feature "
        "you are teaching.",
        (),
    ),
    (
        "board_plot_function",
        "plot_function",
        "new",
        PlotFunctionArgs,
        "Plot y = f(x) on an existing coordinate system. The backend samples the "
        "expression safely; you only supply the expression and optional x domain.",
        (),
    ),
    (
        "board_plot_points",
        "plot_points",
        "new",
        PlotPointsArgs,
        "Plot labelled data points on an existing coordinate system. Points are data "
        "coordinates, not normalized board positions.",
        (),
    ),
    (
        "board_add_graph_label",
        "add_graph_label",
        "new",
        AddGraphLabelArgs,
        "Add a text label at a data-coordinate point on an existing coordinate system.",
        (),
    ),
    (
        "board_erase",
        "erase",
        "new",
        EraseArgs,
        "Erase the whole board (scope='all') or a rectangular region (scope='region').",
        (),
    ),
    (
        "board_erase_element",
        "erase_element",
        "existing",
        EraseElementArgs,
        "Remove one existing element by id (soft delete, replay-safe).",
        (),
    ),
    (
        "board_highlight",
        "highlight",
        "new",
        HighlightArgs,
        "Annotate 1-20 existing elements with an underline/circle/box/strike/arrow. "
        "All target_ids must exist.",
        (),
    ),
    (
        "board_clear",
        "clear",
        "none",
        ClearArgs,
        "Wipe the entire board document. Use sparingly, when starting a clean slate "
        "for a new phase of the lesson.",
        (),
    ),
    (
        "board_move_element",
        "move_element",
        "existing",
        MoveElementArgs,
        "Move an existing element to an absolute normalized position or by a delta.",
        (),
    ),
)

_LESSON_SPECS: tuple[tuple[str, type[BaseModel], str], ...] = (
    (
        "lesson_next_concept",
        LessonNextConceptArgs,
        "Advance the lesson to the next concept (or a specific concept id). Call only "
        "after the current concept has been taught and checked.",
    ),
    (
        "lesson_update_progress",
        LessonUpdateProgressArgs,
        "Record observed progress / understanding for the current concept. Use after "
        "the student demonstrates understanding.",
    ),
    (
        "lesson_ask_question",
        LessonAskQuestionArgs,
        "Ask the student one clear question about the current concept.",
    ),
    (
        "lesson_check_answer",
        LessonCheckAnswerArgs,
        "Evaluate the student's answer and update mastery from that evidence. Follow "
        "with spoken feedback to the student.",
    ),
    (
        "lesson_revisit_concept",
        LessonRevisitConceptArgs,
        "Go back to an earlier concept and re-teach it with a different approach.",
    ),
    (
        "lesson_finish",
        LessonFinishArgs,
        "Finish the lesson with a short summary. Only call when the student is done or "
        "time is up.",
    ),
)

TOOL_SPECS: tuple[ToolSpec, ...] = tuple(
    ToolSpec(
        name=name,
        kind="board",
        event_type=event_type,
        target_mode=target_mode,
        args_model=args_model,
        description=description,
        allowed_element_types=allowed,
    )
    for name, event_type, target_mode, args_model, description, allowed in _BOARD_SPECS
) + tuple(
    ToolSpec(
        name=name,
        kind="lesson",
        event_type=None,
        target_mode="none",
        args_model=args_model,
        description=description,
    )
    for name, args_model, description in _LESSON_SPECS
)

TOOL_SPECS_BY_NAME: dict[str, ToolSpec] = {spec.name: spec for spec in TOOL_SPECS}

BOARD_TOOL_NAMES: tuple[str, ...] = tuple(spec.name for spec in TOOL_SPECS if spec.kind == "board")
LESSON_TOOL_NAMES: tuple[str, ...] = tuple(
    spec.name for spec in TOOL_SPECS if spec.kind == "lesson"
)
ALL_TOOL_NAMES: tuple[str, ...] = tuple(spec.name for spec in TOOL_SPECS)

#: tool -> board event type (used by contract-parity tests and the registry).
BOARD_TOOL_EVENT_TYPES: dict[str, str] = {
    spec.name: spec.event_type for spec in TOOL_SPECS if spec.kind == "board" and spec.event_type
}


# ---------------------------------------------------------------------------
# JSON schema emission (OpenAI/Qwen function-tool format)
# ---------------------------------------------------------------------------

_STRIP_KEYS = {"title", "default", "examples", "$defs", "discriminator"}


def _inline_refs(node: Any, defs: dict[str, Any], depth: int = 0) -> Any:
    """Recursively inline ``$defs`` refs so providers without local refs work."""
    if depth > 12:  # defensive: argument models are shallow by construction
        return node
    if isinstance(node, dict):
        if "$ref" in node:
            ref = node["$ref"]
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                target = defs.get(ref.split("/")[-1], {})
                merged = _inline_refs(target, defs, depth + 1)
                if isinstance(merged, dict):
                    node = {**merged, **{k: v for k, v in node.items() if k != "$ref"}}
        return {k: _inline_refs(v, defs, depth + 1) for k, v in node.items() if k not in _STRIP_KEYS}
    if isinstance(node, list):
        return [_inline_refs(item, defs, depth + 1) for item in node]
    return node


def _clean_json_schema(node: Any) -> Any:
    """Collapse ``anyOf: [X, null]`` to ``X`` and strip metadata noise."""
    if isinstance(node, dict):
        node = {key: _clean_json_schema(value) for key, value in node.items()}
        any_of = node.get("anyOf")
        if isinstance(any_of, list):
            variants = [v for v in any_of if not (isinstance(v, dict) and v.get("type") == "null")]
            has_null = len(variants) != len(any_of)
            if len(variants) == 1:
                collapsed = variants[0]
                rest = {k: v for k, v in node.items() if k != "anyOf"}
                node = {**collapsed, **rest}
            elif len(variants) > 1:
                node["anyOf"] = variants
            if has_null:
                node.pop("default", None)
        if node.get("type") == "object":
            node.setdefault("additionalProperties", False)
            node.setdefault("properties", {})
        return node
    if isinstance(node, list):
        return [_clean_json_schema(item) for item in node]
    return node


def openai_tool_schema(name: str, description: str, args_model: type[BaseModel]) -> dict[str, Any]:
    """Build one OpenAI-style function-tool schema from a pydantic argument model."""
    raw = args_model.model_json_schema(mode="validation")
    defs = raw.get("$defs", {})
    parameters = _clean_json_schema(_inline_refs(raw, defs))
    parameters.pop("$defs", None)
    parameters.setdefault("type", "object")
    parameters.setdefault("additionalProperties", False)
    parameters.setdefault("properties", {})
    # ``required`` must be explicit per contract; pydantic already emits it, but
    # never let an empty required list disappear for zero-arg tools.
    if "required" not in parameters:
        parameters["required"] = []
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters,
        },
    }


def openai_tool_schemas() -> list[dict[str, Any]]:
    """All 28 Qwen function tool schemas, in registry order."""
    return [spec.openai_schema() for spec in TOOL_SPECS]


__all__ = [
    "ALL_TOOL_NAMES",
    "BOARD_TOOL_EVENT_TYPES",
    "BOARD_TOOL_NAMES",
    "LESSON_TOOL_NAMES",
    "TOOL_SPECS",
    "TOOL_SPECS_BY_NAME",
    "ToolSpec",
    "StrictModel",
    "Position",
    "Size",
    "Delta",
    "Region",
    "DataPoint",
    "CanvasPoint",
    "RangeTuple",
    "ColorStr",
    "openai_tool_schema",
    "openai_tool_schemas",
    # argument models
    "WriteTextArgs",
    "WriteHeadingArgs",
    "WriteDefinitionArgs",
    "WriteBulletListArgs",
    "UpdateTextArgs",
    "WriteEquationArgs",
    "UpdateEquationArgs",
    "HighlightEquationArgs",
    "DrawLineArgs",
    "DrawShapeArgs",
    "DrawPathArgs",
    "DrawArrowArgs",
    "DrawDiagramArgs",
    "CreateCoordinateSystemArgs",
    "PlotFunctionArgs",
    "PlotPointsArgs",
    "AddGraphLabelArgs",
    "EraseArgs",
    "EraseElementArgs",
    "HighlightArgs",
    "ClearArgs",
    "MoveElementArgs",
    "LessonNextConceptArgs",
    "LessonUpdateProgressArgs",
    "LessonAskQuestionArgs",
    "LessonCheckAnswerArgs",
    "LessonRevisitConceptArgs",
    "LessonFinishArgs",
]
