"""Generative drawing protocol (Phase 4).

A versioned, general-purpose vector drawing protocol that Qwen3.7-Flash uses to
describe *original* educational illustrations. There are no predefined
templates: the model decides what to draw, which structures are needed, their
geometry, styling, layering, and how the drawing is progressively revealed.

The protocol is a strict JSON data format — never executable code, never raw
SVG/HTML. Everything here is validated server-side with pydantic before the
plan is persisted/broadcast as a ``draw_plan`` board event.

Coordinate system: normalized board space, x/y in [0, 1], origin top-left,
y grows downward (same space every other board event uses). Angles are degrees
(0 = +x axis, clockwise positive because y is flipped on screen).

Bounds & caps (anti-abuse):
* at most :data:`MAX_ELEMENTS` elements and :data:`MAX_STAGES` stages,
* at most :data:`MAX_PATH_SEGMENTS` segments per path and
  :data:`MAX_POINTS` points per polyline/polygon,
* text <= :data:`MAX_TEXT_LENGTH` chars (control characters rejected),
* every coordinate finite in [0, 1], stroke widths bounded, opacities bounded.
"""
from __future__ import annotations

import math
import re
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.board.palette import sanitize_illustration_color

PROTOCOL_NAME = "clasrum.drawing"
PROTOCOL_VERSION = 1

# --- limits -----------------------------------------------------------------
MAX_ELEMENTS = 120
MAX_STAGES = 12
MAX_STAGES_ELEMENTS = 120
MAX_PATH_SEGMENTS = 160
MAX_POINTS = 400
MAX_DASH_SEGMENTS = 16
MAX_TEXT_LENGTH = 500
MAX_TITLE = 200
MAX_DESCRIPTION = 240
MAX_ELEMENT_ID = 64
MAX_LAYERS = 6  # layer 0..5
MAX_STAGE_INDEX = MAX_STAGES

MIN_DURATION_MS = 60
MAX_DURATION_MS = 6000
MAX_DELAY_MS = 4000
MAX_PAUSE_MS = 3000

ANGLE_LIMIT = 720.0

_ELEMENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

ChalkRoles = ("primary", "secondary", "blue", "yellow", "red", "green")


def _validate_color(value: str) -> str:
    """Sanitize a generated-plan colour into the bright illustration palette.

    Generated plans are illustrations, not prose.  Legacy role names and any
    Qwen-supplied hex are mapped to an approved high-contrast colour so dark,
    gray and off-white ink can never reach the renderer.
    """
    return sanitize_illustration_color(value)


ColorStr = Annotated[
    str,
    BeforeValidator(_validate_color),
    Field(pattern=r"^#[0-9A-F]{6}$"),
]

Normalized = Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)]
StrokeWidth = Annotated[float, Field(ge=0.2, le=4.0, allow_inf_nan=False)]
Opacity = Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)]


def _round(value: float) -> float:
    rounded = round(float(value), 4)
    return 0.0 if rounded == 0 else rounded


class ProtocolModel(BaseModel):
    """Base model for protocol nodes. Unknown keys are ignored (forward
    compatible) but every known field is strictly typed and bounded."""

    model_config = ConfigDict(
        extra="ignore",
        allow_inf_nan=False,
        str_strip_whitespace=False,
        validate_default=True,
    )


class ProtocolPoint(ProtocolModel):
    """A normalized point."""

    x: Normalized
    y: Normalized

    def to_payload(self) -> dict[str, float]:
        return {"x": _round(self.x), "y": _round(self.y)}


class ProtocolSize(ProtocolModel):
    """A normalized size."""

    w: Annotated[float, Field(gt=0.0, le=1.0, allow_inf_nan=False)]
    h: Annotated[float, Field(gt=0.0, le=1.0, allow_inf_nan=False)]

    def to_payload(self) -> dict[str, float]:
        return {"w": _round(self.w), "h": _round(self.h)}


class PathSegment(ProtocolModel):
    """One segment of an arbitrary path.

    ``op`` selects the segment type:

    * ``move``  — ``point`` (starts a new subpath)
    * ``line``  — ``point``
    * ``quad``  — ``control`` + ``point`` (quadratic Bézier)
    * ``cubic`` — ``c1`` + ``c2`` + ``point`` (cubic Bézier)
    * ``arc``   — ``center`` + ``rx`` + ``ry`` + ``start_angle`` + ``sweep_angle``
    * ``close`` — closes the current subpath
    """

    op: Literal["move", "line", "quad", "cubic", "arc", "close"]
    point: ProtocolPoint | None = None
    control: ProtocolPoint | None = None
    c1: ProtocolPoint | None = None
    c2: ProtocolPoint | None = None
    center: ProtocolPoint | None = None
    rx: Annotated[float, Field(gt=0.0, le=1.0, allow_inf_nan=False)] | None = None
    ry: Annotated[float, Field(gt=0.0, le=1.0, allow_inf_nan=False)] | None = None
    start_angle: Annotated[
        float, Field(ge=-ANGLE_LIMIT, le=ANGLE_LIMIT, allow_inf_nan=False)
    ] | None = None
    sweep_angle: Annotated[
        float, Field(ge=-ANGLE_LIMIT, le=ANGLE_LIMIT, allow_inf_nan=False)
    ] | None = None

    @model_validator(mode="after")
    def _require_fields(self) -> "PathSegment":
        if self.op in {"move", "line"} and self.point is None:
            raise ValueError(f"path segment op={self.op!r} requires point")
        if self.op == "quad" and (self.control is None or self.point is None):
            raise ValueError("path segment op='quad' requires control and point")
        if self.op == "cubic" and (self.c1 is None or self.c2 is None or self.point is None):
            raise ValueError("path segment op='cubic' requires c1, c2 and point")
        if self.op == "arc":
            missing = [
                name
                for name, value in (
                    ("center", self.center),
                    ("rx", self.rx),
                    ("ry", self.ry),
                    ("start_angle", self.start_angle),
                    ("sweep_angle", self.sweep_angle),
                )
                if value is None
            ]
            if missing:
                raise ValueError(f"path segment op='arc' requires {', '.join(missing)}")
        return self

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"op": self.op}
        if self.point is not None:
            payload["point"] = self.point.to_payload()
        if self.control is not None:
            payload["control"] = self.control.to_payload()
        if self.c1 is not None:
            payload["c1"] = self.c1.to_payload()
        if self.c2 is not None:
            payload["c2"] = self.c2.to_payload()
        if self.center is not None:
            payload["center"] = self.center.to_payload()
        if self.rx is not None:
            payload["rx"] = _round(self.rx)
        if self.ry is not None:
            payload["ry"] = _round(self.ry)
        if self.start_angle is not None:
            payload["start_angle"] = _round(self.start_angle)
        if self.sweep_angle is not None:
            payload["sweep_angle"] = _round(self.sweep_angle)
        return payload


class ProtocolStyle(ProtocolModel):
    """Stroke/fill appearance of one element."""

    stroke_color: ColorStr | None = None
    stroke_width: StrokeWidth | None = None
    stroke_opacity: Opacity | None = None
    fill_color: ColorStr | None = None
    fill_opacity: Opacity | None = None
    line_cap: Literal["butt", "round", "square"] | None = None
    line_join: Literal["miter", "round", "bevel"] | None = None
    dash: (
        Annotated[
            list[Annotated[float, Field(ge=0.05, le=50.0, allow_inf_nan=False)]],
            Field(min_length=1, max_length=MAX_DASH_SEGMENTS),
        ]
        | None
    ) = None

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for key in (
            "stroke_color",
            "stroke_width",
            "stroke_opacity",
            "fill_color",
            "fill_opacity",
            "line_cap",
            "line_join",
        ):
            value = getattr(self, key)
            if value is not None:
                payload[key] = _round(value) if isinstance(value, float) else value
        if self.dash:
            payload["dash"] = [_round(v) for v in self.dash]
        return payload


class LineGeometry(ProtocolModel):
    start: ProtocolPoint
    end: ProtocolPoint

    def to_payload(self) -> dict[str, Any]:
        return {"start": self.start.to_payload(), "end": self.end.to_payload()}


class PolylineGeometry(ProtocolModel):
    points: Annotated[
        list[ProtocolPoint], Field(min_length=2, max_length=MAX_POINTS)
    ]
    closed: bool = False
    smooth: bool = True

    def to_payload(self) -> dict[str, Any]:
        return {
            "points": [p.to_payload() for p in self.points],
            "closed": self.closed,
            "smooth": self.smooth,
        }


class RectangleGeometry(ProtocolModel):
    position: ProtocolPoint
    size: ProtocolSize
    corner_radius: Annotated[float, Field(ge=0.0, le=0.5, allow_inf_nan=False)] | None = None

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "position": self.position.to_payload(),
            "size": self.size.to_payload(),
        }
        if self.corner_radius is not None:
            payload["corner_radius"] = _round(self.corner_radius)
        return payload


class EllipseGeometry(ProtocolModel):
    center: ProtocolPoint
    rx: Annotated[float, Field(gt=0.0, le=1.0, allow_inf_nan=False)]
    ry: Annotated[float, Field(gt=0.0, le=1.0, allow_inf_nan=False)]

    def to_payload(self) -> dict[str, Any]:
        return {
            "center": self.center.to_payload(),
            "rx": _round(self.rx),
            "ry": _round(self.ry),
        }


class ArcGeometry(ProtocolModel):
    center: ProtocolPoint
    rx: Annotated[float, Field(gt=0.0, le=1.0, allow_inf_nan=False)]
    ry: Annotated[float, Field(gt=0.0, le=1.0, allow_inf_nan=False)]
    start_angle: Annotated[float, Field(ge=-ANGLE_LIMIT, le=ANGLE_LIMIT, allow_inf_nan=False)]
    sweep_angle: Annotated[float, Field(ge=-ANGLE_LIMIT, le=ANGLE_LIMIT, allow_inf_nan=False)]

    def to_payload(self) -> dict[str, Any]:
        return {
            "center": self.center.to_payload(),
            "rx": _round(self.rx),
            "ry": _round(self.ry),
            "start_angle": _round(self.start_angle),
            "sweep_angle": _round(self.sweep_angle),
        }


class BezierGeometry(ProtocolModel):
    start: ProtocolPoint
    control: ProtocolPoint | None = None  # quadratic
    c1: ProtocolPoint | None = None  # cubic
    c2: ProtocolPoint | None = None
    end: ProtocolPoint

    @model_validator(mode="after")
    def _quadratic_or_cubic(self) -> "BezierGeometry":
        if self.control is None and (self.c1 is None or self.c2 is None):
            raise ValueError("bezier requires control (quadratic) or c1+c2 (cubic)")
        return self

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"start": self.start.to_payload()}
        if self.control is not None:
            payload["control"] = self.control.to_payload()
        if self.c1 is not None:
            payload["c1"] = self.c1.to_payload()
        if self.c2 is not None:
            payload["c2"] = self.c2.to_payload()
        payload["end"] = self.end.to_payload()
        return payload


class PathGeometry(ProtocolModel):
    segments: Annotated[list[PathSegment], Field(min_length=1, max_length=MAX_PATH_SEGMENTS)]

    @model_validator(mode="after")
    def _starts_with_move(self) -> "PathGeometry":
        if self.segments[0].op == "close":
            raise ValueError("path must not start with a close segment")
        return self

    def to_payload(self) -> dict[str, Any]:
        return {"segments": [s.to_payload() for s in self.segments]}


class TextGeometry(ProtocolModel):
    text: Annotated[str, Field(min_length=1, max_length=MAX_TEXT_LENGTH)]
    position: ProtocolPoint
    size: ProtocolSize | None = None
    align: Literal["left", "center", "right"] = "left"
    font_scale: Annotated[float, Field(ge=0.5, le=2.5, allow_inf_nan=False)] | None = None

    @field_validator("text")
    @classmethod
    def _clean_text(cls, value: str) -> str:
        if _CONTROL_CHARS_RE.search(value):
            raise ValueError("text must not contain control characters")
        return value

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "text": self.text,
            "position": self.position.to_payload(),
            "align": self.align,
        }
        if self.size is not None:
            payload["size"] = self.size.to_payload()
        if self.font_scale is not None:
            payload["font_scale"] = _round(self.font_scale)
        return payload


class ArrowGeometry(ProtocolModel):
    start: ProtocolPoint
    end: ProtocolPoint
    curve: Annotated[float, Field(ge=-1.0, le=1.0, allow_inf_nan=False)] = 0.0

    def to_payload(self) -> dict[str, Any]:
        return {
            "start": self.start.to_payload(),
            "end": self.end.to_payload(),
            "curve": _round(self.curve),
        }


ElementKind = Literal[
    "line",
    "polyline",
    "polygon",
    "rectangle",
    "ellipse",
    "arc",
    "quad_bezier",
    "cubic_bezier",
    "path",
    "text",
    "arrow",
]

_GEOMETRY_FOR_KIND: dict[str, tuple[type[ProtocolModel], ...]] = {
    "line": (LineGeometry,),
    "polyline": (PolylineGeometry,),
    "polygon": (PolylineGeometry,),
    "rectangle": (RectangleGeometry,),
    "ellipse": (EllipseGeometry,),
    "arc": (ArcGeometry,),
    "quad_bezier": (BezierGeometry,),
    "cubic_bezier": (BezierGeometry,),
    "path": (PathGeometry,),
    "text": (TextGeometry,),
    "arrow": (ArrowGeometry,),
}

_CLOSED_KINDS = frozenset({"polygon", "rectangle", "ellipse"})


class DrawingElement(ProtocolModel):
    """One drawable element of a generated illustration."""

    id: Annotated[str, StringConstraints(pattern=_ELEMENT_ID_RE, max_length=MAX_ELEMENT_ID)]
    kind: ElementKind
    geometry: ProtocolModel  # replaced by kind-specific type below
    style: ProtocolStyle | None = None
    layer: Annotated[int, Field(ge=0, le=MAX_LAYERS - 1)] = 0
    stage: Annotated[int, Field(ge=1, le=MAX_STAGE_INDEX)] = 1
    duration_ms: Annotated[int, Field(ge=MIN_DURATION_MS, le=MAX_DURATION_MS)] = 600
    delay_ms: Annotated[int, Field(ge=0, le=MAX_DELAY_MS)] = 0
    clip_id: (
        Annotated[str, StringConstraints(pattern=_ELEMENT_ID_RE, max_length=MAX_ELEMENT_ID)]
        | None
    ) = None

    @model_validator(mode="before")
    @classmethod
    def _bind_geometry(cls, data: Any) -> Any:
        """Swap the placeholder geometry for the kind-specific model."""
        if isinstance(data, dict):
            kind = data.get("kind")
            raw_geometry = data.get("geometry")
            model = _GEOMETRY_FOR_KIND.get(str(kind))
            if model is not None and not isinstance(raw_geometry, ProtocolModel):
                if not isinstance(raw_geometry, dict):
                    raise ValueError("geometry must be an object")
                data = {**data, "geometry": model[0].model_validate(raw_geometry)}
            elif model is None:
                raise ValueError(f"unsupported element kind {kind!r}")
        return data

    @property
    def closed(self) -> bool:
        if self.kind in _CLOSED_KINDS:
            return True
        if self.kind == "polyline":
            return bool(self.geometry.closed)  # type: ignore[attr-defined]
        if self.kind == "path":
            return any(
                segment.op == "close"
                for segment in self.geometry.segments  # type: ignore[attr-defined]
            )
        return False

    @model_validator(mode="after")
    def _geometry_matches_kind(self) -> "DrawingElement":
        expected = _GEOMETRY_FOR_KIND[str(self.kind)][0]
        if not isinstance(self.geometry, expected):
            raise ValueError(f"geometry does not match kind {self.kind!r}")
        # Fill only makes sense on closed shapes (renderer would ignore it anyway).
        if not self.closed and self.style is not None and self.style.fill_color is not None:
            raise ValueError(
                f"fill_color is only valid on closed elements (kind={self.kind!r})"
            )
        return self

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "geometry": self.geometry.to_payload(),
            "layer": self.layer,
            "stage": self.stage,
            "duration_ms": self.duration_ms,
            "delay_ms": self.delay_ms,
        }
        if self.style is not None:
            style = self.style.to_payload()
            if style:
                payload["style"] = style
        if self.clip_id is not None:
            payload["clip_id"] = self.clip_id
        return payload


class DrawingStage(ProtocolModel):
    """A sequential drawing stage (progressive reveal step)."""

    stage: Annotated[int, Field(ge=1, le=MAX_STAGE_INDEX)]
    description: Annotated[
        str, Field(min_length=1, max_length=MAX_DESCRIPTION)
    ] = ""
    element_ids: (
        Annotated[list[str], Field(min_length=0, max_length=MAX_STAGES_ELEMENTS)] | None
    ) = None
    pause_ms: Annotated[int, Field(ge=0, le=MAX_PAUSE_MS)] = 0

    @field_validator("element_ids", mode="before")
    @classmethod
    def _default_empty(cls, value: Any) -> Any:
        return value if value is not None else []

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "stage": self.stage,
            "description": self.description,
            "pause_ms": self.pause_ms,
        }
        if self.element_ids:
            payload["element_ids"] = list(self.element_ids)
        return payload


class DrawingPlan(ProtocolModel):
    """A complete generative drawing plan produced by Qwen3.7-Flash."""

    protocol: Literal["clasrum.drawing"] = PROTOCOL_NAME
    version: Literal[1] = PROTOCOL_VERSION
    title: (
        Annotated[str, Field(min_length=1, max_length=MAX_TITLE)] | None
    ) = None
    elements: Annotated[list[DrawingElement], Field(min_length=1, max_length=MAX_ELEMENTS)]
    stages: Annotated[list[DrawingStage], Field(max_length=MAX_STAGES)] = Field(
        default_factory=list
    )
    # Board-intent side channels carried by the model response. These are not
    # part of the drawable protocol, so they are validated leniently and
    # passed through untouched for the intent handler to consume.
    focus: dict[str, Any] | None = None
    triggers: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("focus", mode="before")
    @classmethod
    def _drop_invalid_focus(cls, value: Any) -> Any:
        return value if isinstance(value, dict) else None

    @field_validator("triggers", mode="before")
    @classmethod
    def _drop_invalid_triggers(cls, value: Any) -> Any:
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, dict)]

    @model_validator(mode="after")
    def _validate_references(self) -> "DrawingPlan":
        ids = [element.id for element in self.elements]
        if len(ids) != len(set(ids)):
            raise ValueError("element ids must be unique")
        id_set = set(ids)

        # Clip references must point at an earlier, closed element.
        position = {element_id: index for index, element_id in enumerate(ids)}
        for element in self.elements:
            if element.clip_id is None:
                continue
            target_index = position.get(element.clip_id)
            if target_index is None or target_index >= position[element.id]:
                raise ValueError(
                    f"clip_id {element.clip_id!r} must reference an earlier element"
                )
            target = self.elements[target_index]
            if not target.closed:
                raise ValueError(f"clip_id {element.clip_id!r} must reference a closed element")

        # Stage references and stage element lists must resolve.
        declared_stages = {stage.stage for stage in self.stages}
        for element in self.elements:
            if element.stage not in declared_stages and self.stages:
                raise ValueError(
                    f"element {element.id!r} references undeclared stage {element.stage}"
                )
        for stage in self.stages:
            for element_id in stage.element_ids or []:
                if element_id not in id_set:
                    raise ValueError(
                        f"stage {stage.stage} references unknown element id {element_id!r}"
                    )

        # At least one drawable (non-text) element: an illustration, not just prose.
        if not any(element.kind != "text" for element in self.elements):
            raise ValueError("drawing plan must contain at least one drawable element")
        return self

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "protocol": self.protocol,
            "version": self.version,
            "elements": [element.to_payload() for element in self.elements],
        }
        if self.title is not None:
            payload["title"] = self.title
        if self.stages:
            payload["stages"] = [stage.to_payload() for stage in self.stages]
        if self.focus:
            payload["focus"] = dict(self.focus)
        if self.triggers:
            payload["triggers"] = [dict(trigger) for trigger in self.triggers]
        return payload


class DrawingPlanError(ValueError):
    """A drawing plan failed protocol validation (safe to show to the model)."""


def parse_drawing_plan(data: Any) -> DrawingPlan:
    """Validate untrusted model output and return a :class:`DrawingPlan`.

    Raises :class:`DrawingPlanError` with a short, model-actionable message.
    """
    if not isinstance(data, dict):
        raise DrawingPlanError("drawing plan must be a JSON object")
    try:
        return DrawingPlan.model_validate(data)
    except Exception as exc:  # pydantic ValidationError and friends
        message = _summarize_validation_error(exc)
        raise DrawingPlanError(message) from None


def _summarize_validation_error(exc: Exception) -> str:
    errors = getattr(exc, "errors", None)
    parts: list[str] = []
    if callable(errors):
        for error in errors()[:4]:
            location = ".".join(str(item) for item in error.get("loc", ()) if str(item) != "__root__")
            message = str(error.get("msg", "invalid value"))
            message = message.replace("Input should be", "must be")
            parts.append(f"{location}: {message}" if location else message)
    text = "; ".join(parts) if parts else str(exc)
    return text[:800]


def plan_total_duration_ms(plan: MappingLike) -> int:
    """Total reveal duration (ms) of a validated plan payload.

    Sequential timeline: elements play in (stage, declaration) order with their
    ``delay_ms`` pause before the stroke; after each stage a ``pause_ms`` gap
    separates stages.
    """
    elements = plan.get("elements") if isinstance(plan, dict) else None
    stages = plan.get("stages") if isinstance(plan, dict) else None
    if not isinstance(elements, list) or not elements:
        return 0
    by_id: dict[str, dict[str, Any]] = {}
    order: list[dict[str, Any]] = []
    for element in elements:
        if isinstance(element, dict) and isinstance(element.get("id"), str):
            by_id[element["id"]] = element
            order.append(element)

    stage_numbers = []
    if isinstance(stages, list):
        for stage in stages:
            if isinstance(stage, dict) and isinstance(stage.get("stage"), int):
                stage_numbers.append(stage["stage"])
    stage_numbers = sorted(set(stage_numbers)) if stage_numbers else [1]

    elapsed = 0
    scheduled: set[str] = set()
    for stage_number in stage_numbers:
        members = [e for e in order if int(e.get("stage", 1)) == stage_number]
        if isinstance(stages, list):
            for stage in stages:
                if isinstance(stage, dict) and stage.get("stage") == stage_number:
                    listed = stage.get("element_ids")
                    if isinstance(listed, list):
                        explicit = [by_id[str(i)] for i in listed if str(i) in by_id]
                        members = explicit + [e for e in members if e not in explicit]
                    pause = stage.get("pause_ms", 0)
                    break
            else:
                pause = 0
        else:
            pause = 0
        for element in members:
            element_id = str(element.get("id"))
            if element_id in scheduled:
                continue
            scheduled.add(element_id)
            delay = _int_clamp(element.get("delay_ms", 0), 0, MAX_DELAY_MS)
            duration = _int_clamp(
                element.get("duration_ms", MIN_DURATION_MS), MIN_DURATION_MS, MAX_DURATION_MS
            )
            start = elapsed + delay
            elapsed = start + duration
        if stage_number != stage_numbers[-1]:
            elapsed += _int_clamp(pause, 0, MAX_PAUSE_MS)
    # Any elements outside declared stages draw after them.
    for element in order:
        element_id = str(element.get("id"))
        if element_id in scheduled:
            continue
        scheduled.add(element_id)
        duration = _int_clamp(
            element.get("duration_ms", MIN_DURATION_MS), MIN_DURATION_MS, MAX_DURATION_MS
        )
        elapsed += duration
    return elapsed


def plan_element_bounds(plan: MappingLike) -> list[tuple[float, float, float, float]]:
    """Normalized bounding boxes of all elements, for viewport/annotation use."""
    boxes: list[tuple[float, float, float, float]] = []
    elements = plan.get("elements") if isinstance(plan, dict) else None
    if not isinstance(elements, list):
        return boxes
    for element in elements:
        if not isinstance(element, dict):
            continue
        box = _element_bounds(element)
        if box is not None:
            boxes.append(box)
    return boxes


def _element_bounds(element: MappingLike) -> tuple[float, float, float, float] | None:
    kind = str(element.get("kind", ""))
    geometry = element.get("geometry")
    if not isinstance(geometry, dict):
        return None
    points: list[tuple[float, float]] = []

    def collect(point: Any) -> None:
        if isinstance(point, dict) and _num(point.get("x")) is not None and _num(point.get("y")) is not None:
            points.append((float(point["x"]), float(point["y"])))

    if kind == "line" or kind == "arrow":
        collect(geometry.get("start"))
        collect(geometry.get("end"))
        curve = _num(geometry.get("curve"))
        if kind == "arrow" and curve and curve != 0.0 and len(points) == 2:
            # Approximate the quadratic bulge with its midpoint control.
            (x1, y1), (x2, y2) = points
            cx = x1 + (x2 - x1) * 0.5 + curve * (y1 - y2) * 0.3
            cy = y1 + (y2 - y1) * 0.5 + curve * (x2 - x1) * 0.3
            points.append((cx, cy))
    elif kind in {"polyline", "polygon"}:
        for raw in geometry.get("points") or []:
            collect(raw)
    elif kind == "rectangle":
        position = geometry.get("position")
        size = geometry.get("size")
        if isinstance(position, dict) and isinstance(size, dict):
            x = _num(position.get("x"))
            y = _num(position.get("y"))
            w = _num(size.get("w"))
            h = _num(size.get("h"))
            if None not in (x, y, w, h):
                return (float(x), float(y), float(w), float(h))
    elif kind == "ellipse" or kind == "arc":
        center = geometry.get("center")
        rx = _num(geometry.get("rx"))
        ry = _num(geometry.get("ry"))
        if isinstance(center, dict) and rx and ry:
            cx = _num(center.get("x"))
            cy = _num(center.get("y"))
            if cx is not None and cy is not None:
                return (float(cx) - float(rx), float(cy) - float(ry), float(rx) * 2, float(ry) * 2)
    elif kind in {"quad_bezier", "cubic_bezier"}:
        for key in ("start", "control", "c1", "c2", "end"):
            collect(geometry.get(key))
    elif kind == "path":
        for segment in geometry.get("segments") or []:
            if isinstance(segment, dict):
                for key in ("point", "control", "c1", "c2", "center"):
                    collect(segment.get(key))
    elif kind == "text":
        position = geometry.get("position")
        size = geometry.get("size")
        text = str(geometry.get("text", ""))
        if isinstance(position, dict):
            x = _num(position.get("x"))
            y = _num(position.get("y"))
            if x is not None and y is not None:
                if isinstance(size, dict) and _num(size.get("w")) and _num(size.get("h")):
                    return (float(x), float(y), float(size["w"]), float(size["h"]))
                width = min(0.9, max(0.03, len(text) * 0.008))
                return (float(x), float(y), width, 0.03)
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    if isinstance(value, str):
        try:
            parsed = float(value)
            if math.isfinite(parsed):
                return parsed
        except ValueError:
            return None
    return None


def _int_clamp(value: Any, low: int, high: int) -> int:
    number = _num(value)
    if number is None:
        return low
    return max(low, min(high, int(round(number))))


class MappingLike(dict):  # pragma: no cover - typing alias for payload dicts
    """Structural alias: a validated plan payload mapping."""


__all__ = [
    "DrawingPlan",
    "DrawingPlanError",
    "DrawingElement",
    "DrawingStage",
    "PROTOCOL_NAME",
    "PROTOCOL_VERSION",
    "MAX_ELEMENTS",
    "MAX_STAGES",
    "MAX_PATH_SEGMENTS",
    "MAX_POINTS",
    "MAX_TEXT_LENGTH",
    "parse_drawing_plan",
    "plan_total_duration_ms",
    "plan_element_bounds",
]
