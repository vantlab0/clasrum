"""Deterministic blackboard layout engine.

The text/visual model describes *what* to place; this module decides *where*.
It is the single backend authority for physical placement on the board.

Coordinate system
-----------------
There is exactly one convention, shared with Flutter: normalized coordinates,
origin at the top-left, ``x/y/w/h`` in ``[0, 1]``.  No pixel conversions happen
here - the client applies its own viewport transform.

Design rules
------------
* Existing elements are immutable obstacles; only new elements get positions.
* A new unrelated element is never placed over existing content.  If nothing
  fits (even after a bounded shrink inside readability limits) the placement
  fails explicitly with :class:`LayoutNoSpaceError` instead of silently
  overlapping or going off-board.
* Elements of the same layout group may overlap by design (table cells inside
  table bounds, labels on shapes, connected diagram parts).  A group is
  positioned and moved as one unit.
* Placement is deterministic for a given document + request sequence, and all
  state mutation is guarded by a re-entrant lock so concurrent tool calls
  cannot claim the same free region.
"""
from __future__ import annotations

import asyncio
import math
import threading
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

EPS = 1e-6

# One lock per lesson serializes "reconstruct document -> allocate region ->
# append event", so concurrent tool calls cannot claim the same free space.
_layout_locks: dict[str, asyncio.Lock] = {}


def board_layout_lock(lesson_id: str) -> asyncio.Lock:
    """Return the async layout/commit lock for a lesson."""
    lock = _layout_locks.get(lesson_id)
    if lock is None:
        lock = asyncio.Lock()
        _layout_locks[lesson_id] = lock
    return lock

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LayoutConfig:
    """Tunable geometry for the normalized board canvas."""

    width: float = 1.0
    height: float = 1.0
    margin_x: float = 0.04
    margin_y: float = 0.045
    section_gap: float = 0.032
    element_gap: float = 0.014
    max_content_width: float = 0.72
    min_width: float = 0.06
    min_height: float = 0.028
    step_x: float = 0.01
    step_y: float = 0.01

    @property
    def safe(self) -> "Rect":
        return Rect(
            self.margin_x,
            self.margin_y,
            max(0.1, self.width - 2 * self.margin_x),
            max(0.1, self.height - 2 * self.margin_y),
        )

    @property
    def column_width(self) -> float:
        """Width of the natural lecture-writing column."""
        return min(self.max_content_width, self.safe.w)


DEFAULT_CONFIG = LayoutConfig()

# Named regions are expressed once, in the same normalized space as elements.
REGION_RECTS: dict[str, tuple[float, float, float, float]] = {
    "left": (0.04, 0.10, 0.44, 0.82),
    "right": (0.52, 0.10, 0.44, 0.82),
    "top": (0.06, 0.06, 0.88, 0.30),
    "bottom": (0.06, 0.64, 0.88, 0.30),
    "center": (0.24, 0.22, 0.52, 0.56),
    "bottom_left": (0.04, 0.60, 0.44, 0.34),
    "bottom_right": (0.52, 0.60, 0.44, 0.34),
    "top_left": (0.04, 0.06, 0.44, 0.34),
    "top_right": (0.52, 0.06, 0.44, 0.34),
    "full": (0.04, 0.045, 0.92, 0.91),
}

DEFAULT_REGION = "full"
FALLBACK_ORDER = (
    "full",
    "left",
    "right",
    "bottom",
    "top",
    "center",
    "bottom_left",
    "bottom_right",
    "top_left",
    "top_right",
)

# Conservative footprints by logical action kind (used when no text is given).
DEFAULT_SIZES: dict[str, tuple[float, float]] = {
    "heading": (0.42, 0.075),
    "text": (0.40, 0.065),
    "definition": (0.42, 0.10),
    "bullet_list": (0.38, 0.18),
    "equation": (0.32, 0.09),
    "diagram": (0.36, 0.28),
    "coordinate_system": (0.34, 0.28),
    "graph": (0.36, 0.30),
    "plot": (0.36, 0.30),
    "table": (0.46, 0.26),
    "illustration": (0.40, 0.32),
    "label": (0.18, 0.05),
    "shape": (0.20, 0.14),
    "arrow": (0.20, 0.10),
    "node": (0.18, 0.08),
    "annotation": (0.18, 0.06),
}


@dataclass(frozen=True)
class TextStyleMetrics:
    """Backend mirror of Flutter's ``ChalkTextStyleSpec``.

    Keep these numbers synchronized with
    ``app/lib/blackboard/render/chalk_text.dart``.  A parity test parses that
    file so a drift fails the backend suite instead of silently under-reserving
    board space.
    """

    fraction: float
    letter_spacing_em: float = 0.01
    line_height: float = 1.28
    glyph_width_factor: float = 0.54


TEXT_STYLE_SPECS: dict[str, TextStyleMetrics] = {
    "h1": TextStyleMetrics(0.076, 0.022),
    "h2": TextStyleMetrics(0.062, 0.020),
    "h3": TextStyleMetrics(0.054, 0.018),
    "h4": TextStyleMetrics(0.047, 0.015),
    "h5": TextStyleMetrics(0.041, 0.012),
    "h6": TextStyleMetrics(0.036, 0.010),
    "heading": TextStyleMetrics(0.076, 0.022),
    "subheading": TextStyleMetrics(0.062, 0.020),
    "body": TextStyleMetrics(0.032, 0.010),
    "small": TextStyleMetrics(0.026, 0.010),
    "equation": TextStyleMetrics(0.038, 0.005),
    "definition": TextStyleMetrics(0.032, 0.010),
    "definitionTerm": TextStyleMetrics(0.038, 0.015),
    "bullet": TextStyleMetrics(0.031, 0.010),
    "numbered": TextStyleMetrics(0.031, 0.010),
    "label": TextStyleMetrics(0.024, 0.010),
    "caption": TextStyleMetrics(0.021, 0.010),
    "note": TextStyleMetrics(0.027, 0.010),
    "code": TextStyleMetrics(0.026, 0.0, line_height=1.35, glyph_width_factor=0.62),
}

# Backwards-compatible aliases for older callers/tests.  The authoritative
# values now live in TEXT_STYLE_SPECS.
BODY_FONT_NORM_H = TEXT_STYLE_SPECS["body"].fraction
HEADING_SCALE = {
    level: TEXT_STYLE_SPECS[f"h{level}"].fraction / BODY_FONT_NORM_H
    for level in range(1, 7)
}

TEXT_LIKE_EVENT_TYPES = frozenset(
    {
        "write_text", "write_heading", "write_definition", "write_bullet_list",
        "update_text", "add_text", "text",
    }
)


class LayoutNoSpaceError(RuntimeError):
    """Raised when an element cannot be placed without overlapping/off-board."""

    code = "no_board_space"

    def __init__(self, message: str = "no free board region for element", *, kind: str = "element"):
        super().__init__(message)
        self.kind = kind
        self.retryable = True


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------


def _r(value: float) -> float:
    rounded = round(float(value), 4)
    return 0.0 if rounded == 0 else rounded


@dataclass
class Rect:
    x: float
    y: float
    w: float
    h: float

    @property
    def x2(self) -> float:
        return self.x + self.w

    @property
    def y2(self) -> float:
        return self.y + self.h

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    @property
    def area(self) -> float:
        return max(0.0, self.w) * max(0.0, self.h)

    def clamp_to_board(self, config: LayoutConfig = DEFAULT_CONFIG) -> "Rect":
        w = min(max(self.w, config.min_width), config.safe.w)
        h = min(max(self.h, config.min_height), config.safe.h)
        x = max(config.safe.x, min(self.x, config.safe.x2 - w))
        y = max(config.safe.y, min(self.y, config.safe.y2 - h))
        return Rect(_r(x), _r(y), _r(w), _r(h))

    def intersects(self, other: "Rect") -> bool:
        return not (
            self.x2 <= other.x + EPS
            or other.x2 <= self.x + EPS
            or self.y2 <= other.y + EPS
            or other.y2 <= self.y + EPS
        )

    def contains(self, other: "Rect") -> bool:
        return (
            other.x >= self.x - EPS
            and other.y >= self.y - EPS
            and other.x2 <= self.x2 + EPS
            and other.y2 <= self.y2 + EPS
        )

    def scaled(self, factor: float, config: LayoutConfig = DEFAULT_CONFIG) -> "Rect":
        w = max(config.min_width, self.w * factor)
        h = max(config.min_height, self.h * factor)
        return Rect(self.x, self.y, min(w, config.safe.w), min(h, config.safe.h)).clamp_to_board(config)

    def as_payload(self) -> dict[str, float]:
        return {"x": _r(self.x), "y": _r(self.y), "w": _r(self.w), "h": _r(self.h)}


@dataclass
class Occupied:
    rect: Rect
    element_id: str | None = None
    group_id: str | None = None
    parent_id: str | None = None
    kind: str = "element"

    def related_to(self, group_id: str | None, parent_id: str | None) -> bool:
        """True when a candidate belongs to the same intentional layout group."""
        if group_id and self.group_id == group_id:
            return True
        if parent_id and (self.element_id == parent_id or self.group_id == parent_id):
            return True
        return bool(group_id and self.parent_id == group_id)


@dataclass
class LayoutState:
    """Occupied regions for one planning pass."""

    entries: list[Occupied] = field(default_factory=list)
    config: LayoutConfig = DEFAULT_CONFIG
    region_cursors: dict[str, tuple[float, float]] = field(default_factory=dict)

    @property
    def occupied(self) -> list[Rect]:
        return [entry.rect for entry in self.entries]

    def add(self, rect: Rect, *, element_id: str | None = None, group_id: str | None = None,
            parent_id: str | None = None, kind: str = "element") -> None:
        self.entries.append(Occupied(rect, element_id, group_id, parent_id, kind))

    def collides(self, rect: Rect, *, group_id: str | None = None, parent_id: str | None = None) -> bool:
        # Keep a tiny chalk gap even for fallback scans: each side contributes
        # half of element_gap, so any accepted pair is separated by >= element_gap.
        half_gap = self.config.element_gap / 2.0
        padded = Rect(
            rect.x - half_gap,
            rect.y - half_gap,
            rect.w + self.config.element_gap,
            rect.h + self.config.element_gap,
        )
        return any(
            padded.intersects(entry.rect)
            for entry in self.entries
            if not entry.related_to(group_id, parent_id)
        )


@dataclass
class PlacementResult:
    ok: bool
    kind: str
    rect: Rect | None = None
    region: str | None = None
    reason: str | None = None

    @property
    def payload(self) -> dict[str, float]:
        if self.rect is None:
            return {}
        return self.rect.as_payload()


# ---------------------------------------------------------------------------
# Existing-document geometry
# ---------------------------------------------------------------------------


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def rect_from_element(element: Mapping[str, Any], config: LayoutConfig = DEFAULT_CONFIG) -> Rect | None:
    """Best-effort bounds for an existing board element (never raises)."""
    if element.get("removed_at_sequence") is not None:
        return None
    payload = element.get("payload") if isinstance(element.get("payload"), Mapping) else {}
    position = element.get("position") or payload.get("position") or {}
    size = element.get("size") or payload.get("size")
    rect: Rect | None = None
    if isinstance(position, Mapping):
        x, y = _as_float(position.get("x")), _as_float(position.get("y"))
        if x is not None and y is not None:
            w, h = 0.18, 0.08
            if isinstance(size, Mapping):
                w = _as_float(size.get("w")) or w
                h = _as_float(size.get("h")) or h
            elif _is_text_element(element):
                # Direct realtime writes persist every semantic level as
                # `write_text`; infer the real footprint from the payload so a
                # size-less heading cannot be treated as a tiny body line.
                w, h = estimate_text_size(
                    _text_from_element(element),
                    style=str(payload.get("style") or element.get("style") or "body"),
                    heading_level=payload.get("heading_level") or element.get("heading_level"),
                    font_scale=_as_float(payload.get("font_scale")) or 1.0,
                    max_width=payload.get("max_width"),
                    config=config,
                )
            else:
                etype = str(element.get("type") or "")
                if "equation" in etype:
                    w, h = 0.32, 0.09
                elif "heading" in etype:
                    w, h = 0.42, 0.08
                elif "bullet" in etype:
                    w, h = 0.38, 0.18
                elif any(t in etype for t in ("coordinate", "graph", "plot")):
                    w, h = 0.36, 0.30
                elif "diagram" in etype or "plan" in etype:
                    w, h = 0.36, 0.28
                elif "definition" in etype:
                    w, h = 0.42, 0.10
            rect = Rect(x, y, max(w, 0.02), max(h, 0.02))
    if rect is None:
        points = payload.get("points") or []
        if not points and isinstance(payload.get("start"), Mapping) and isinstance(payload.get("end"), Mapping):
            points = [payload["start"], payload["end"]]
        xs, ys = [], []
        for point in points:
            if not isinstance(point, Mapping):
                continue
            px, py = _as_float(point.get("x")), _as_float(point.get("y"))
            if px is not None and py is not None:
                xs.append(px)
                ys.append(py)
        if xs and ys:
            pad = 0.01
            rect = Rect(
                min(xs) - pad, min(ys) - pad,
                max(xs) - min(xs) + 2 * pad, max(ys) - min(ys) + 2 * pad,
            )
    return rect.clamp_to_board(config) if rect is not None else None


def _derived_flow_cursors(
    document: Mapping[str, Any] | None, config: LayoutConfig = DEFAULT_CONFIG
) -> dict[str, tuple[float, float]]:
    """Derive a durable "write the next line here" cursor from the document.

    A manager is rebuilt for every tool call, so an in-memory cursor alone
    resets to the top of the board.  Deriving it from the most recently created
    live text element keeps consecutive `board_write_on_board` calls in the
    natural reading flow instead of first-fitting into earlier holes.
    """
    cursors: dict[str, tuple[int, float, float]] = {}
    for element in (document or {}).get("elements", []) or []:
        if not isinstance(element, Mapping) or not _is_text_element(element):
            continue
        if element.get("removed_at_sequence") is not None:
            continue
        rect = rect_from_element(element, config)
        if rect is None:
            continue
        key = "left_column" if rect.cx < 0.5 else "main_column"
        sequence = int(element.get("created_sequence") or element.get("updated_sequence") or 0)
        y = min(config.safe.y2, rect.y2 + config.element_gap)
        previous = cursors.get(key)
        if previous is None or sequence >= previous[0]:
            cursors[key] = (sequence, rect.x, y)
    return {
        key: (x, y)
        for key, (_sequence, x, y) in cursors.items()
    }


def build_layout_state(document: Mapping[str, Any] | None, config: LayoutConfig = DEFAULT_CONFIG) -> LayoutState:
    state = LayoutState(config=config)
    if not document:
        return state
    for element in document.get("elements", []) or []:
        if not isinstance(element, Mapping):
            continue
        rect = rect_from_element(element, config)
        if rect is None:
            continue
        group_id = str(element.get("group_id") or (element.get("payload") or {}).get("group_id") or "") or None
        parent_id = element.get("element_id")
        state.add(
            rect,
            element_id=str(element.get("element_id") or "") or None,
            group_id=group_id,
            parent_id=str(parent_id) if parent_id else None,
            kind=str(element.get("type") or "element"),
        )
    state.region_cursors.update(_derived_flow_cursors(document, config))
    return state


def region_rect(name: str | None, config: LayoutConfig = DEFAULT_CONFIG) -> Rect:
    raw = REGION_RECTS.get(str(name or DEFAULT_REGION))
    if raw is None:
        raw = REGION_RECTS[DEFAULT_REGION]
    return Rect(*raw).clamp_to_board(config)


def region_occupancy(state: LayoutState, name: str) -> float:
    target = region_rect(name, state.config)
    if target.area <= 0:
        return 0.0
    overlap = 0.0
    for entry in state.entries:
        dx = max(0.0, min(target.x2, entry.rect.x2) - max(target.x, entry.rect.x))
        dy = max(0.0, min(target.y2, entry.rect.y2) - max(target.y, entry.rect.y))
        overlap += dx * dy
    return min(1.0, overlap / target.area)


def choose_region(
    hint: str | None,
    state: LayoutState,
    *,
    relative_to: str | None = None,
    document: Mapping[str, Any] | None = None,
) -> str:
    normalized = _normalize_region_hint(hint)
    if normalized:
        return normalized
    if relative_to and document:
        for element in document.get("elements", []) or []:
            if element.get("element_id") != relative_to or element.get("removed_at_sequence") is not None:
                continue
            rect = rect_from_element(element, state.config)
            if rect is None:
                continue
            if rect.cx < 0.5 and rect.cy + rect.h < 0.72:
                return "right"
            if rect.y2 < 0.65:
                return "bottom"
            return "top"
    best, best_score = DEFAULT_REGION, math.inf
    for name in FALLBACK_ORDER:
        score = region_occupancy(state, name) + 0.001 * FALLBACK_ORDER.index(name)
        if score < best_score:
            best, best_score = name, score
    return best


# ---------------------------------------------------------------------------
# Size estimation
# ---------------------------------------------------------------------------


def _text_metrics(style: str | None, heading_level: int | None = None) -> TextStyleMetrics:
    """Resolve a style name to the exact Flutter chalk typography metrics."""
    normalized = str(style or "body").strip().lower()
    level = int(heading_level or 0)
    if normalized in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        level = int(normalized[1:])
    if normalized in {"heading", "subheading"} or level > 0:
        level = min(6, max(1, level or 1))
        return TEXT_STYLE_SPECS[f"h{level}"]
    return TEXT_STYLE_SPECS.get(normalized, TEXT_STYLE_SPECS["body"])


def _is_text_placement(kind: str, payload: Mapping[str, Any]) -> bool:
    style = str(payload.get("style") or "").lower()
    return (
        str(kind).lower() in TEXT_LIKE_EVENT_TYPES
        or str(kind).lower() in {"text", "equation"}
        or style in TEXT_STYLE_SPECS
        or style in {"h1", "h2", "h3", "h4", "h5", "h6"}
    )


def _is_text_element(element: Mapping[str, Any]) -> bool:
    payload = element.get("payload") if isinstance(element.get("payload"), Mapping) else {}
    return _is_text_placement(str(element.get("type") or ""), payload)


def _text_from_element(element: Mapping[str, Any]) -> str:
    payload = element.get("payload") if isinstance(element.get("payload"), Mapping) else {}
    value = payload.get("text")
    if value is None:
        value = payload.get("items")
    if isinstance(value, (list, tuple)):
        return " ".join(str(item) for item in value)
    return str(value or "")


def _wrapped_line_count(text: str, advance: float, usable_w: float) -> int:
    """Conservative line count for Flutter's ``TextPainter`` word wrapping.

    ``ceil(total_advance / usable_w)`` is only a lower bound: trailing space at
    every word boundary can add another line. Simulate greedy wrapping and keep
    the larger result so a reservation is never shorter than the painted run.
    """
    char_lines = max(1, math.ceil(len(text) * advance / max(usable_w, EPS)))
    lines = 1
    used = 0.0
    for word in text.split():
        word_w = len(word) * advance
        separator = advance if used > 0 else 0.0
        if used + separator + word_w <= usable_w + EPS:
            used += separator + word_w
            continue
        lines += 1
        used = word_w
        if word_w > usable_w:
            # Flutter soft-wraps a single word that cannot fit on one line.
            extra = math.ceil((word_w - usable_w) / max(usable_w, EPS))
            lines += extra
            used -= extra * usable_w
    return max(char_lines, lines)


def estimate_text_size(
    text: str,
    *,
    style: str = "body",
    heading_level: int | None = None,
    font_scale: float = 1.0,
    max_width: float | None = None,
    config: LayoutConfig | None = None,
) -> tuple[float, float]:
    """Estimate normalized ``(w, h)`` reserved for a chalk text element.

    The implementation mirrors Flutter's ``ChalkTextStyleSpec`` and
    ``TextPainter`` wrapping closely enough that the reservation is never
    smaller than the visible chalk.  It intentionally rounds generously:
    positive letter spacing and word-wrap boundaries are over-estimated rather
    than under-estimated.
    """
    safe_text = " ".join(str(text or "").split())
    if not safe_text:
        return (0.12, 0.04)
    metrics = _text_metrics(style, heading_level)
    scale = max(0.5, min(2.5, float(font_scale or 1.0)))
    font_h = metrics.fraction * scale
    # Flutter applies letterSpacing after every glyph, including the last one.
    advance = font_h * (metrics.glyph_width_factor + metrics.letter_spacing_em)
    usable_w = max(
        0.12,
        min(
            config.max_content_width if config else DEFAULT_CONFIG.max_content_width,
            float(max_width or 0)
            or (config.max_content_width if config else DEFAULT_CONFIG.max_content_width),
        ),
    )
    natural_w = max(advance * 2, len(safe_text) * advance)
    # No 12-line cap: generated/lesson text must never under-reserve height.
    lines = _wrapped_line_count(safe_text, advance, usable_w)
    height = lines * font_h * metrics.line_height + font_h * 0.35
    # Flutter's TextPainter wraps at maxWidth, so the painted box can never be
    # wider than the usable width (allow one glyph of letter-spacing slack).
    reserved_w = min(natural_w, usable_w + advance)
    return (_r(max(0.08, min(0.92, reserved_w))), _r(max(0.035, min(0.60, height))))


def estimate_equation_size(latex: str, *, font_scale: float = 1.0) -> tuple[float, float]:
    text = str(latex or "").strip()
    scale = max(0.5, min(2.5, float(font_scale or 1.0)))
    width = max(0.16, min(0.82, len(text) * 0.018 * scale + 0.04))
    height = max(0.07, min(0.26, 0.075 * scale))
    if any(token in text for token in ("\\frac", "\\sum", "\\int", "\\sqrt")):
        height = min(0.30, height * 1.5)
    return (_r(width), _r(height))


def estimate_table_size(rows: int, columns: int, *, cell_chars: int = 12) -> tuple[float, float]:
    """Conservative footprint for a table generated as part of a visual."""
    cols = max(1, min(12, int(columns or 1)))
    count = max(1, min(30, int(rows or 1)))
    width = min(DEFAULT_CONFIG.max_content_width, max(0.20, cols * 0.09 + max(0, cell_chars - 8) * 0.004 * cols))
    height = min(0.55, max(0.10, count * 0.045 + 0.04))
    return (_r(width), _r(height))


def _default_size(action: Mapping[str, Any], config: LayoutConfig = DEFAULT_CONFIG) -> tuple[float, float]:
    payload = action.get("payload") if isinstance(action.get("payload"), Mapping) else action
    kind = str(action.get("kind") or action.get("type") or (payload or {}).get("style") or "text").lower()
    style = str((payload or {}).get("style") or "").lower()
    text = str((payload or {}).get("text") or (payload or {}).get("latex") or "")
    font_scale = _as_float((payload or {}).get("font_scale")) or 1.0

    if kind in {"write_equation", "equation"} or style == "equation":
        return estimate_equation_size(text or str((payload or {}).get("latex") or ""), font_scale=font_scale)
    if kind in {"write_text", "write_heading", "add_text", "text"} or style in {
        "heading", "subheading", "body", "small", "label", "definition", "caption", "note", "code",
        "h1", "h2", "h3", "h4", "h5", "h6",
    }:
        return estimate_text_size(
            text,
            style=style,
            heading_level=(payload or {}).get("heading_level"),
            font_scale=font_scale,
            max_width=(payload or {}).get("max_width"),
            config=config,
        )
    if kind in {"table", "comparison_table", "data_table"}:
        return estimate_table_size(
            int((payload or {}).get("rows") or 4), int((payload or {}).get("columns") or 3)
        )
    mapping = {
        "add_node": "node",
        "write_bullet_list": "bullet_list",
        "write_definition": "definition",
        "draw_diagram": "diagram",
        "create_coordinate_system": "coordinate_system",
        "plot_function": "plot",
        "plot_points": "plot",
        "add_graph_label": "label",
        "annotate": "annotation",
        "draw_arrow": "arrow",
        "draw_shape": "shape",
    }
    key = mapping.get(kind, kind)
    return DEFAULT_SIZES.get(key, (0.30, 0.10))


def _normalize_region_hint(hint: str | None) -> str | None:
    if not hint:
        return None
    value = str(hint).strip().lower()
    aliases = {"next_line": None, "auto": None, "left": "left", "right": "right", "center": "center"}
    return value if value in REGION_RECTS else aliases.get(value)


def _side_to_region(side: str | None) -> str | None:
    value = str(side or "auto").strip().lower()
    if value == "left":
        return "left"
    if value == "right":
        return "right"
    return None


# ---------------------------------------------------------------------------
# Layout manager
# ---------------------------------------------------------------------------


class BlackboardLayoutManager:
    """The single backend authority for blackboard element placement.

    One instance represents one planning pass over a document snapshot.  It is
    safe to share an instance across concurrent callers: reservations are
    serialized with a re-entrant lock.
    """

    def __init__(self, document: Mapping[str, Any] | None = None, config: LayoutConfig | None = None):
        self.config = config or DEFAULT_CONFIG
        self._lock = threading.RLock()
        self.refresh(document)

    def refresh(self, document: Mapping[str, Any] | None) -> None:
        with self._lock:
            self.document = document or {"elements": []}
            # build_layout_state derives durable flow cursors from persisted
            # elements; do not clear them here.
            self.state = build_layout_state(self.document, self.config)

    # -- public placement API ------------------------------------------------

    def place_element(
        self,
        kind: str,
        *,
        width: float | None = None,
        height: float | None = None,
        payload: Mapping[str, Any] | None = None,
        position_hint: str | None = None,
        side_preference: str | None = None,
        region: str | None = None,
        related_to: str | None = None,
        group_id: str | None = None,
        parent_id: str | None = None,
        element_id: str | None = None,
        section_break: bool = False,
        prefer_horizontal: bool = False,
    ) -> PlacementResult:
        """Place one element and reserve its region.

        Returns a controlled failure result (never an overlapping rect) when
        the board cannot accommodate the element.
        """
        data = dict(payload or {})
        default_w, default_h = _default_size({"kind": kind, "payload": data}, self.config)
        w = _as_float(width) if width is not None else None
        h = _as_float(height) if height is not None else None
        w = max(self.config.min_width, min(self.config.safe.w, w if w and w > 0 else default_w))
        h = max(self.config.min_height, min(self.config.safe.h, h if h and h > 0 else default_h))
        size = Rect(0, 0, _r(w), _r(h))

        with self._lock:
            hint = _normalize_region_hint(position_hint) or _normalize_region_hint(region)
            side = _side_to_region(side_preference)
            anchor = self._element_rect(related_to) or self._element_rect(data.get("related_to"))

            candidates: list[tuple[str, Rect]] = []
            # 1. Side-by-side when requested and there is room next to the anchor.
            if anchor is not None and (prefer_horizontal or side in {"left", "right"}):
                beside = self._beside_anchor(anchor, size, side)
                if beside is not None:
                    candidates.append((self._region_of(beside), beside))
                # Fallback: directly below the anchor (vertical layout).
                below = Rect(anchor.x, anchor.y2 + self.config.element_gap, size.w, size.h)
                candidates.append((self._region_of(below), below.clamp_to_board(self.config)))
            # 2. Explicit side preference without an anchor.
            if side and anchor is None:
                candidates.append((side, region_rect(side, self.config)))
            # 3. Explicit region hint.
            if hint:
                candidates.append((hint, region_rect(hint, self.config)))
            # 4. Natural vertical reading flow (the default for teaching content).
            candidates.append(self._flow_position(size, section_break=section_break, prefer_left=(side == "left")))
            # 5. Any remaining region, in priority order.
            for name in FALLBACK_ORDER:
                candidates.append((name, region_rect(name, self.config)))

            # Text reserves must stay font-accurate: shrinking only the box
            # while leaving font_scale/text untouched makes the renderer overflow
            # the reservation.  Text either fits at full size or fails cleanly.
            scales = (1.0,) if _is_text_placement(kind, data) else (1.0, 0.9, 0.8, 0.7)
            for scale in scales:
                target = size.scaled(scale, self.config)
                if target.w < self.config.min_width - EPS or target.h < self.config.min_height - EPS:
                    continue
                for name, area in candidates:
                    rect = self._scan_area(area, target, group_id=group_id, parent_id=parent_id)
                    if rect is None:
                        continue
                    self.state.add(
                        rect, element_id=element_id, group_id=group_id,
                        parent_id=parent_id, kind=kind,
                    )
                    self._advance_flow(name, rect, section_break=section_break)
                    return PlacementResult(True, kind, rect, name)
            return PlacementResult(False, kind, None, None, "no_space")

    def place_text(self, payload: Mapping[str, Any], *, default_region: str | None = None) -> dict[str, Any]:
        """Position a ``write_text`` payload.  Raises on layout failure."""
        data = dict(payload)
        style = str(data.get("style") or "body").lower()
        kind = "equation" if style == "equation" else "text"
        result = self.place_element(
            kind,
            payload=data,
            position_hint=data.get("position_hint") or default_region,
            side_preference=data.get("side_preference"),
            region=default_region,
            related_to=data.get("related_concept"),
            element_id=data.get("element_id"),
        )
        if not result.ok or result.rect is None:
            raise LayoutNoSpaceError(kind=kind)
        data["position"] = {"x": result.rect.x, "y": result.rect.y}
        data["size"] = {"w": result.rect.w, "h": result.rect.h}
        data["region"] = result.region
        if not _as_float(data.get("max_width")):
            data["max_width"] = result.rect.w
        return data

    def place_payload(
        self,
        payload: Mapping[str, Any],
        *,
        kind: str = "text",
        default_region: str | None = None,
    ) -> dict[str, Any]:
        """Compatibility wrapper around :meth:`place_element` for arbitrary payloads."""
        data = dict(payload)
        result = self.place_element(
            kind, payload=data, region=default_region,
            position_hint=data.get("position_hint"),
            side_preference=data.get("side_preference"),
            group_id=data.get("group_id"),
            parent_id=data.get("parent_id"),
            element_id=data.get("element_id"),
        )
        if not result.ok or result.rect is None:
            raise LayoutNoSpaceError(kind=kind)
        data["position"] = {"x": result.rect.x, "y": result.rect.y}
        data["size"] = {"w": result.rect.w, "h": result.rect.h}
        data["region"] = result.region
        if _is_text_placement(kind, data) and not _as_float(data.get("max_width")):
            data["max_width"] = result.rect.w
        return data

    def place_group(
        self,
        items: Iterable[Mapping[str, Any]],
        *,
        preferred_region: str | None = None,
        side_preference: str | None = None,
        group_id: str | None = None,
        related_to: str | None = None,
        kind: str = "diagram",
    ) -> PlacementResult:
        """Place a complete visual (table/diagram/graph) as one rigid group.

        Each item needs ``dx``/``dy`` (relative to the group origin) and
        ``width``/``height``.  Internal relationships are preserved exactly:
        either every item is placed or the whole group fails.
        """
        entries = [dict(item) for item in items]
        if not entries:
            return PlacementResult(False, kind, None, None, "empty_group")
        bounds = self._group_bounds(entries)
        # Only internal members share a group id.  The group's top-level
        # footprint is an ordinary obstacle, so two unrelated visuals can never
        # be exempted from collision checks by a shared literal id.
        internal_group_id = group_id or f"group:{kind}:{entries[0].get('id') or 'anonymous'}"
        result = self.place_element(
            kind,
            width=bounds.w, height=bounds.h,
            region=preferred_region,
            position_hint=preferred_region,
            side_preference=side_preference,
            related_to=related_to,
            element_id=group_id or str(entries[0].get("id") or "") or None,
        )
        if not result.ok or result.rect is None:
            return result
        origin_x, origin_y = result.rect.x - bounds.x, result.rect.y - bounds.y
        for item in entries:
            rect = Rect(
                origin_x + float(item.get("dx", 0.0)),
                origin_y + float(item.get("dy", 0.0)),
                max(self.config.min_width, float(item.get("width", 0.1))),
                max(self.config.min_height, float(item.get("height", 0.05))),
            ).clamp_to_board(self.config)
            self.state.add(
                rect, element_id=str(item.get("id") or "") or None,
                group_id=internal_group_id, parent_id=internal_group_id, kind=str(item.get("kind") or kind),
            )
            item["position"] = {"x": rect.x, "y": rect.y}
            item["size"] = {"w": rect.w, "h": rect.h}
        result.payload_items = entries  # type: ignore[attr-defined]
        return result

    def find_region_for_visual(
        self,
        *,
        width: float,
        height: float,
        preferred: str | None = None,
    ) -> tuple[str, Rect]:
        """Return a free region big enough for a visual (legacy adapter)."""
        with self._lock:
            result = self.place_element(
                "visual", width=width, height=height,
                region=preferred, position_hint=preferred,
                element_id=None,
            )
        if result.ok and result.rect is not None:
            return result.region or DEFAULT_REGION, result.rect
        raise LayoutNoSpaceError(kind="visual")

    def reserve(self, rect: Rect, *, element_id: str | None = None, group_id: str | None = None,
                parent_id: str | None = None, kind: str = "element") -> None:
        """Explicitly reserve a region (used by callers committing a plan)."""
        with self._lock:
            self.state.add(rect.clamp_to_board(self.config), element_id=element_id,
                           group_id=group_id, parent_id=parent_id, kind=kind)

    def available_area(self, region: str | None = None) -> Rect:
        """Largest free axis-aligned band inside a region (approximate)."""
        area = region_rect(region or DEFAULT_REGION, self.config)
        with self._lock:
            overlapping = [e.rect for e in self.state.entries if e.rect.intersects(area)]
        if not overlapping:
            return area
        bottom = max((r.y2 for r in overlapping), default=area.y)
        if bottom + self.config.min_height <= area.y2:
            return Rect(area.x, bottom + self.config.element_gap, area.w,
                        area.y2 - bottom - self.config.element_gap)
        return Rect(area.x, area.y, 0.0, 0.0)

    # -- internals -----------------------------------------------------------

    def _element_rect(self, element_id: Any) -> Rect | None:
        if not element_id:
            return None
        for element in self.document.get("elements", []) or []:
            if element.get("element_id") == element_id and element.get("removed_at_sequence") is None:
                return rect_from_element(element, self.config)
        return None

    def _region_of(self, rect: Rect) -> str:
        for name, raw in REGION_RECTS.items():
            if Rect(*raw).contains(rect):
                return name
        return DEFAULT_REGION

    def _beside_anchor(self, anchor: Rect, size: Rect, side: str | None) -> Rect | None:
        gap = self.config.element_gap
        safe = self.config.safe
        if side == "left":
            x = max(safe.x, anchor.x - size.w - gap)
        else:
            x = anchor.x2 + gap
        rect = Rect(x, anchor.y, size.w, size.h)
        if rect.x2 > safe.x2 + EPS or rect.y2 > safe.y2 + EPS:
            return None
        return rect.clamp_to_board(self.config)

    def _scan_area(
        self,
        area: Rect,
        target: Rect,
        *,
        group_id: str | None,
        parent_id: str | None,
    ) -> Rect | None:
        """First non-colliding slot inside *area*, scanned top-to-bottom."""
        safe = self.config.safe
        x_start = max(safe.x, area.x)
        y_start = max(safe.y, area.y)
        x_end = min(safe.x2, area.x2)
        y_end = min(safe.y2, area.y2)
        if x_end - x_start < target.w - EPS or y_end - y_start < target.h - EPS:
            return None
        y = y_start
        while y + target.h <= y_end + EPS:
            x = x_start
            while x + target.w <= x_end + EPS:
                rect = Rect(x, y, target.w, target.h).clamp_to_board(self.config)
                if not self.state.collides(rect, group_id=group_id, parent_id=parent_id):
                    return rect
                x += self.config.step_x
            y += self.config.step_y
        return None

    def _flow_position(self, size: Rect, *, section_break: bool, prefer_left: bool) -> tuple[str, Rect]:
        """Sequential lecture-writing cursor: top-to-bottom, then next column."""
        key = "left_column" if prefer_left else "main_column"
        cursor = self.state.region_cursors.get(key)
        safe = self.config.safe
        if cursor is None:
            x, y = safe.x, safe.y
        else:
            x, y = cursor
            if section_break:
                y += self.config.section_gap
        if y + size.h > safe.y2:
            # Start a fresh column/band; _scan_area still rejects collisions.
            y = safe.y
            x = safe.x if x <= safe.x + EPS else safe.x + self.config.column_width + self.config.element_gap
        x = max(safe.x, min(x, safe.x2 - max(size.w, self.config.min_width)))
        width = max(0.0, min(self.config.column_width, safe.x2 - x))
        return key, Rect(x, y, width, max(0.0, safe.y2 - y))

    def _advance_flow(self, region: str, rect: Rect, *, section_break: bool) -> None:
        if region not in {"main_column", "left_column"}:
            return
        y = rect.y2 + self.config.element_gap
        if y + self.config.min_height > self.config.safe.y2:
            y = self.config.safe.y
        self.state.region_cursors[region] = (rect.x, y)

    def _group_bounds(self, items: list[dict[str, Any]]) -> Rect:
        xs = [float(item.get("dx", 0.0)) for item in items]
        ys = [float(item.get("dy", 0.0)) for item in items]
        x2 = [float(item.get("dx", 0.0)) + float(item.get("width", 0.1)) for item in items]
        y2 = [float(item.get("dy", 0.0)) + float(item.get("height", 0.05)) for item in items]
        return Rect(min(xs), min(ys), max(x2) - min(xs), max(y2) - min(ys))


# ---------------------------------------------------------------------------
# Legacy functional API (kept for existing callers and tests)
# ---------------------------------------------------------------------------


def _next_position(region: str, state: LayoutState, w: float, h: float) -> Rect:
    area = region_rect(region, state.config)
    manager = BlackboardLayoutManager.__new__(BlackboardLayoutManager)
    manager.config = state.config
    manager.state = state
    manager.document = {"elements": []}
    result = manager.place_element("element", width=w, height=h, region=region)
    if result.ok and result.rect is not None:
        return result.rect
    # Full-board scan as a last resort; never return an overlapping rectangle.
    safe = state.config.safe
    y = safe.y
    while y + h <= safe.y2 + EPS:
        x = safe.x
        while x + w <= safe.x2 + EPS:
            rect = Rect(x, y, w, h).clamp_to_board(state.config)
            if not state.collides(rect):
                state.add(rect)
                return rect
            x += state.config.step_x
        y += state.config.step_y
    raise LayoutNoSpaceError()


def place_action(
    action: Mapping[str, Any],
    state: LayoutState,
    *,
    document: Mapping[str, Any] | None = None,
    default_region: str | None = None,
) -> dict[str, Any]:
    """Return a copy of *action* with validated ``position`` and ``size``."""
    result = dict(action)
    action_type = str(action.get("type") or "")
    if action_type in {"annotate", "highlight", "focus", "erase", "clear"}:
        return result
    payload = result.get("payload") if isinstance(result.get("payload"), Mapping) else result
    data = dict(payload or {})
    w, h = _default_size({"kind": action.get("kind") or action_type, "payload": data}, state.config)
    size = result.get("size") if isinstance(result.get("size"), Mapping) else data.get("size")
    if isinstance(size, Mapping):
        w = _as_float(size.get("w")) or w
        h = _as_float(size.get("h")) or h

    manager = BlackboardLayoutManager.__new__(BlackboardLayoutManager)
    manager.config = state.config
    manager.state = state
    manager.document = document or {"elements": []}
    # A numeric explicit position is a preference, not an unbreakable command:
    # collisions still relocate it, while semantic hints drive region choice.
    explicit = result.get("position") if isinstance(result.get("position"), Mapping) else data.get("position")
    placement = manager.place_element(
        action.get("kind") or action_type, width=w, height=h, payload=data,
        position_hint=data.get("position_hint") or result.get("region") or default_region,
        side_preference=data.get("side_preference"),
        region=default_region,
        group_id=data.get("group_id"), parent_id=data.get("parent_id"),
    )
    if not placement.ok or placement.rect is None:
        raise LayoutNoSpaceError()
    rect = placement.rect
    result["region"] = placement.region
    result["position"] = {"x": rect.x, "y": rect.y}
    result["size"] = {"w": rect.w, "h": rect.h}
    if explicit is not None and isinstance(result.get("payload"), Mapping):
        result["payload"] = {**data, "position": result["position"], "size": result["size"]}
    return result


def layout_plan(
    plan: Mapping[str, Any],
    document: Mapping[str, Any] | None = None,
    *,
    default_region: str | None = None,
) -> dict[str, Any]:
    """Assign collision-free positions to all placeable actions in a plan."""
    manager = BlackboardLayoutManager(document)
    actions = [place_action(a, manager.state, document=document, default_region=default_region)
               if isinstance(a, Mapping) else a
               for a in (plan.get("actions") or [])]
    out = dict(plan)
    out["actions"] = actions
    return out


__all__ = [
    "BODY_FONT_NORM_H",
    "BOARD_REGIONS",
    "BlackboardLayoutManager",
    "DEFAULT_CONFIG",
    "DEFAULT_REGION",
    "DEFAULT_SIZES",
    "HEADING_SCALE",
    "LayoutConfig",
    "LayoutNoSpaceError",
    "LayoutState",
    "Occupied",
    "PlacementResult",
    "Rect",
    "REGION_RECTS",
    "TEXT_STYLE_SPECS",
    "TextStyleMetrics",
    "board_layout_lock",
    "build_layout_state",
    "choose_region",
    "estimate_equation_size",
    "estimate_table_size",
    "estimate_text_size",
    "layout_plan",
    "place_action",
    "region_occupancy",
    "region_rect",
]

# Alias kept explicit so consumers can discover the single coordinate space.
BOARD_REGIONS = REGION_RECTS
