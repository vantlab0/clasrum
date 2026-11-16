"""Deterministic, lightweight region-based layout engine for board intents.

The text model describes *what* to place; this module decides *where* in the
normalized board coordinate space (``[0, 1]`` for both axes, origin top-left).

Algorithm (by design, no force-directed / heavy dependencies):
* divide the board into named logical regions with preferred rects;
* resolve a target region from explicit hints, ``relative_to`` anchors, or by
  picking the least-occupied region;
* place each action's element inside the region on a simple grid cursor, nudging
  it to the first non-colliding slot when an overlap is detected;
* never move or mutate existing elements – only positions assigned to the new
  actions are adjusted.

All public helpers are pure (no I/O, no DB).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping

# Normalized board bounds.
BOARD_W = 1.0
BOARD_H = 1.0
EPS = 1e-6

# Preferred rects for named regions: (x, y, w, h).
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
    "full": (0.04, 0.08, 0.92, 0.86),
}

DEFAULT_REGION = "right"
FALLBACK_ORDER = (
    "right",
    "left",
    "bottom",
    "top",
    "center",
    "bottom_right",
    "bottom_left",
    "top_right",
    "top_left",
)

# Default element footprint (w, h) by logical action kind.
DEFAULT_SIZES: dict[str, tuple[float, float]] = {
    "heading": (0.40, 0.08),
    "text": (0.38, 0.10),
    "definition": (0.40, 0.12),
    "bullet_list": (0.36, 0.18),
    "equation": (0.30, 0.08),
    "diagram": (0.36, 0.28),
    "coordinate_system": (0.34, 0.28),
    "graph": (0.34, 0.28),
    "plot": (0.34, 0.28),
    "label": (0.18, 0.05),
    "shape": (0.20, 0.14),
    "arrow": (0.20, 0.10),
    "node": (0.18, 0.08),
    "annotation": (0.18, 0.06),
}
GRID_STEP_X = 0.04
GRID_STEP_Y = 0.05
MAX_PLACEMENT_TRIES = 80


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

    def clamp_to_board(self) -> "Rect":
        w = min(max(self.w, 0.02), BOARD_W)
        h = min(max(self.h, 0.02), BOARD_H)
        x = max(0.0, min(self.x, BOARD_W - w))
        y = max(0.0, min(self.y, BOARD_H - h))
        return Rect(x=_r(x), y=_r(y), w=_r(w), h=_r(h))

    def intersects(self, other: "Rect") -> bool:
        return not (
            self.x2 <= other.x + EPS
            or other.x2 <= self.x + EPS
            or self.y2 <= other.y + EPS
            or other.y2 <= self.y + EPS
        )

    def as_payload(self) -> dict[str, float]:
        return {"x": _r(self.x), "y": _r(self.y), "w": _r(self.w), "h": _r(self.h)}


@dataclass
class LayoutState:
    """Mutable placement state for one planning pass."""

    occupied: list[Rect] = field(default_factory=list)
    region_cursors: dict[str, tuple[float, float]] = field(default_factory=dict)

    def add(self, rect: Rect) -> None:
        self.occupied.append(rect)

    def collides(self, rect: Rect) -> bool:
        return any(rect.intersects(existing) for existing in self.occupied)


def _r(value: float) -> float:
    rounded = round(float(value), 4)
    return 0.0 if rounded == 0 else rounded


def rect_from_element(element: Mapping[str, Any]) -> Rect | None:
    """Best-effort bounds for an existing board element."""
    if element.get("removed_at_sequence") is not None:
        return None
    position = element.get("position") or (element.get("payload") or {}).get("position") or {}
    size = element.get("size") or (element.get("payload") or {}).get("size") or {}
    if isinstance(position, dict) and "x" in position and "y" in position:
        try:
            x = float(position["x"])
            y = float(position["y"])
        except (TypeError, ValueError):
            return None
        w = 0.18
        h = 0.08
        if isinstance(size, dict):
            try:
                if "w" in size:
                    w = float(size["w"])
                if "h" in size:
                    h = float(size["h"])
            except (TypeError, ValueError):
                pass
        else:
            # Heuristic by element type.
            etype = str(element.get("type") or "")
            if "equation" in etype:
                w, h = 0.30, 0.08
            elif "heading" in etype:
                w, h = 0.40, 0.08
            elif "bullet" in etype:
                w, h = 0.36, 0.18
            elif "coordinate" in etype or "graph" in etype or "plot" in etype:
                w, h = 0.34, 0.28
            elif "diagram" in etype:
                w, h = 0.36, 0.28
            elif "definition" in etype:
                w, h = 0.40, 0.12
        return Rect(x, y, max(w, 0.02), max(h, 0.02)).clamp_to_board()

    # Fall back to point clouds (lines/paths/arrows).
    payload = element.get("payload") or {}
    points = payload.get("points") or []
    if not points and isinstance(payload.get("start"), dict) and isinstance(payload.get("end"), dict):
        points = [payload["start"], payload["end"]]
    xs: list[float] = []
    ys: list[float] = []
    for point in points:
        if not isinstance(point, dict):
            continue
        try:
            xs.append(float(point["x"]))
            ys.append(float(point["y"]))
        except (TypeError, ValueError, KeyError):
            continue
    if xs and ys:
        pad = 0.01
        return Rect(
            min(xs) - pad,
            min(ys) - pad,
            max(xs) - min(xs) + pad * 2,
            max(ys) - min(ys) + pad * 2,
        ).clamp_to_board()
    return None


def build_layout_state(document: Mapping[str, Any] | None) -> LayoutState:
    state = LayoutState()
    if not document:
        return state
    for element in document.get("elements", []) or []:
        rect = rect_from_element(element)
        if rect is not None:
            state.add(rect)
    return state


def region_rect(name: str | None) -> Rect:
    rect = REGION_RECTS.get(str(name or DEFAULT_REGION)) or REGION_RECTS[DEFAULT_REGION]
    return Rect(*rect)


def region_occupancy(state: LayoutState, name: str) -> float:
    target = region_rect(name)
    if target.w <= 0 or target.h <= 0:
        return 0.0
    area = target.w * target.h
    overlap = 0.0
    for occ in state.occupied:
        ix = max(0.0, min(target.x2, occ.x2) - max(target.x, occ.x))
        iy = max(0.0, min(target.y2, occ.y2) - max(target.y, occ.y))
        overlap += ix * iy
    return overlap / area


def choose_region(
    hint: str | None,
    state: LayoutState,
    *,
    relative_to: str | None = None,
    document: Mapping[str, Any] | None = None,
) -> str:
    if relative_to and document:
        # Place near the referenced element's region.
        for element in document.get("elements", []) or []:
            if element.get("element_id") == relative_to and element.get("removed_at_sequence") is None:
                rect = rect_from_element(element)
                if rect is not None:
                    cx = rect.x + rect.w / 2
                    cy = rect.y + rect.h / 2
                    # Prefer to the right of the anchor, else below, else least occupied.
                    if cx < 0.55:
                        return "right"
                    if cy < 0.55:
                        return "bottom"
                    return "left"
    if hint and hint in REGION_RECTS:
        return hint
    # Pick the least occupied region from the fallback order.
    best = DEFAULT_REGION
    best_score = math.inf
    for name in FALLBACK_ORDER:
        score = region_occupancy(state, name)
        if score < best_score - 1e-9:
            best = name
            best_score = score
            if best_score <= 0.0:
                break
    return best


def _default_size(action: Mapping[str, Any]) -> tuple[float, float]:
    kind = str(action.get("kind") or action.get("type") or "text").lower()
    # Map common action types to footprint kinds.
    mapping = {
        "add_node": "node",
        "add_text": "text",
        "write_text": "text",
        "write_heading": "heading",
        "write_definition": "definition",
        "write_bullet_list": "bullet_list",
        "write_equation": "equation",
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


def _next_position(region: str, state: LayoutState, w: float, h: float) -> Rect:
    area = region_rect(region)
    cursor = state.region_cursors.get(region)
    if cursor is None:
        cursor = (area.x, area.y)
    x, y = cursor

    for _ in range(MAX_PLACEMENT_TRIES):
        candidate = Rect(x, y, w, h).clamp_to_board()
        # Keep inside region when possible.
        if candidate.x + candidate.w > area.x2 + EPS or candidate.y + candidate.h > area.y2 + EPS:
            # Wrap to next column / reset.
            x = area.x
            y += GRID_STEP_Y
            if y + h > area.y2 + EPS:
                # Region full: scan from region origin.
                y = area.y
                x = area.x
            candidate = Rect(x, y, w, h).clamp_to_board()
        if not state.collides(candidate):
            state.region_cursors[region] = (x, y + candidate.h + 0.02)
            return candidate
        # Advance cursor.
        x += GRID_STEP_X
        if x + w > area.x2 + EPS:
            x = area.x
            y += GRID_STEP_Y
        if y + h > area.y2 + EPS:
            y = area.y
            x = area.x
    # Fallback: place at region origin clamped (even if overlapping, better than failing).
    return Rect(area.x, area.y, w, h).clamp_to_board()


def place_action(
    action: Mapping[str, Any],
    state: LayoutState,
    *,
    document: Mapping[str, Any] | None = None,
    default_region: str | None = None,
) -> dict[str, Any]:
    """Return a copy of *action* with a normalized ``position`` (and ``size``).

    Actions that do not need placement (e.g. annotate/highlight targeting existing
    elements) are returned unchanged except for a normalized region tag.
    """
    result = dict(action)
    action_type = str(action.get("type") or "")
    if action_type in {"annotate", "highlight", "focus", "erase", "clear"}:
        return result
    if isinstance(result.get("position"), dict):
        # Trust explicit normalized positions after clamping.
        pos = result["position"]
        try:
            w, h = _default_size(action)
            size = result.get("size") if isinstance(result.get("size"), dict) else {}
            if isinstance(size, dict):
                w = float(size.get("w", w))
                h = float(size.get("h", h))
            rect = Rect(float(pos.get("x", 0)), float(pos.get("y", 0)), w, h).clamp_to_board()
            result["position"] = {"x": rect.x, "y": rect.y}
            result.setdefault("size", {"w": rect.w, "h": rect.h})
            state.add(rect)
            return result
        except (TypeError, ValueError):
            pass

    hint = result.get("region") or default_region
    relative_to = result.get("relative_to")
    region = choose_region(str(hint) if hint else None, state, relative_to=relative_to, document=document)
    w, h = _default_size(action)
    size = result.get("size") if isinstance(result.get("size"), dict) else None
    if isinstance(size, dict):
        try:
            w = max(0.02, min(1.0, float(size.get("w", w))))
            h = max(0.02, min(1.0, float(size.get("h", h))))
        except (TypeError, ValueError):
            pass
    rect = _next_position(region, state, w, h)
    result["region"] = region
    result["position"] = {"x": rect.x, "y": rect.y}
    result["size"] = {"w": rect.w, "h": rect.h}
    state.add(rect)
    return result


def layout_plan(
    plan: Mapping[str, Any],
    document: Mapping[str, Any] | None = None,
    *,
    default_region: str | None = None,
) -> dict[str, Any]:
    """Assign positions to all placeable actions in a structured plan.

    Existing elements in *document* are treated as fixed obstacles. Returns a new
    plan dict with positioned actions (original plan is not mutated).
    """
    state = build_layout_state(document)
    actions = list(plan.get("actions") or [])
    placed: list[dict[str, Any]] = []
    for action in actions:
        if not isinstance(action, Mapping):
            continue
        placed.append(place_action(action, state, document=document, default_region=default_region))
    out = dict(plan)
    out["actions"] = placed
    return out


__all__ = [
    "LayoutState",
    "Rect",
    "build_layout_state",
    "choose_region",
    "layout_plan",
    "place_action",
    "region_occupancy",
    "region_rect",
]
