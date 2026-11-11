"""Board context extraction for note generation (read-only).

Notes need to know what is *currently* on the board. The authoritative board
document is maintained by ``app.board`` and periodically compacted into
``lesson_board_snapshots``; this module loads that snapshot and replays any
later ``lesson_board_events`` with a small, read-only projection reducer. It
never persists anything — it only feeds the notes generator.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import LessonBoardEvent
from app.db.repositories import board_events_after, latest_board_snapshot

#: Event type -> board layer (§2.1: level derived from type).
_LEVELS: dict[str, int] = {
    "write_text": 2,
    "write_heading": 2,
    "write_definition": 2,
    "write_bullet_list": 2,
    "write_code": 2,
    "update_text": 2,
    "write_equation": 3,
    "update_equation": 3,
    "highlight_equation": 3,
    "draw_line": 4,
    "draw_shape": 4,
    "draw_path": 4,
    "draw_arrow": 4,
    "draw_diagram": 4,
    "create_coordinate_system": 5,
    "plot_function": 5,
    "plot_points": 5,
    "add_graph_label": 5,
    "highlight": 6,
}

_MERGE_EVENTS = frozenset({"update_text", "update_equation", "move_element"})


def _element_record(event: LessonBoardEvent) -> dict[str, Any]:
    payload = dict(event.payload or {})
    level = _LEVELS.get(event.type, 2)
    record: dict[str, Any] = {
        "element_id": event.element_id,
        "type": event.type,
        "level": level,
        "payload": payload,
        "created_sequence": int(event.sequence),
        "updated_sequence": None,
    }
    if event.type in _MERGE_EVENTS:
        record["updated_sequence"] = int(event.sequence)
    return record


def apply_event(elements: dict[str, dict[str, Any]], event: LessonBoardEvent) -> None:
    """Apply one board event to the projection dict (idempotent by sequence)."""
    event_type = event.type
    if event_type in {"clear"}:
        elements.clear()
        return
    if event_type == "erase" and (event.payload or {}).get("scope") == "all":
        elements.clear()
        return
    if event_type == "erase_element":
        if event.element_id:
            elements.pop(event.element_id, None)
        return
    if not event.element_id:
        return
    existing = elements.get(event.element_id)
    if event_type in _MERGE_EVENTS:
        if existing is None:
            # Update of an unknown element: keep it as a reference so notes can
            # at least cite the sequence, without inventing content.
            existing = _element_record(event)
            existing["payload"] = dict(event.payload or {})
            elements[event.element_id] = existing
            return
        payload = dict(existing.get("payload") or {})
        for key, value in (event.payload or {}).items():
            if value is not None:
                payload[key] = value
        existing["payload"] = payload
        existing["updated_sequence"] = int(event.sequence)
        return
    record = _element_record(event)
    if existing is not None:
        # Writes with an existing element id behave like a full replacement.
        record["created_sequence"] = existing.get("created_sequence", record["created_sequence"])
    elements[event.element_id] = record


def _snapshot_elements(document: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    elements: dict[str, dict[str, Any]] = {}
    if not document:
        return elements
    raw = document.get("elements") or []
    if isinstance(raw, dict):
        for element_id, element in raw.items():
            if not isinstance(element, dict):
                continue
            record = dict(element)
            record.setdefault("element_id", element_id)
            elements[str(record["element_id"])] = record
        return elements
    for element in raw:
        if not isinstance(element, dict) or not element.get("element_id"):
            continue
        elements[str(element["element_id"])] = dict(element)
    return elements


async def load_board_elements(
    db: AsyncSession, lesson_id: str
) -> list[dict[str, Any]]:
    """Current visible board elements, ordered by creation sequence."""
    snapshot = await latest_board_snapshot(db, lesson_id)
    elements = _snapshot_elements(snapshot.document if snapshot is not None else None)
    after = int(snapshot.last_sequence) if snapshot is not None else 0
    for event in await board_events_after(db, lesson_id, after_sequence=after):
        apply_event(elements, event)
    visible = [
        element
        for element in elements.values()
        if element.get("removed_at_sequence") is None
    ]
    visible.sort(key=lambda e: int(e.get("created_sequence", 0) or 0))
    return visible


def element_text(element: dict[str, Any]) -> str | None:
    """Best human-readable text of a board element (None for pure geometry)."""
    payload = element.get("payload") or {}
    if not isinstance(payload, dict):
        return None
    for key in ("text", "latex", "term", "expression", "title", "label"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    items = payload.get("items")
    if isinstance(items, list):
        rendered = [str(item).strip() for item in items if str(item).strip()]
        if rendered:
            return "; ".join(rendered)
    nodes = payload.get("nodes")
    if isinstance(nodes, list):
        labels = [
            str(node.get("label")).strip()
            for node in nodes
            if isinstance(node, dict) and str(node.get("label") or "").strip()
        ]
        if labels:
            return ", ".join(labels)
    points = payload.get("points")
    if isinstance(points, list) and points:
        return f"path with {len(points)} point(s)"
    return None


def element_heading(element: dict[str, Any]) -> str | None:
    """Heading label if the element is heading-like."""
    if element.get("type") not in {"write_heading", "write_text"}:
        return None
    payload = element.get("payload") or {}
    style = str(payload.get("style") or "")
    if element.get("type") == "write_heading" or style in {"heading", "subheading"}:
        text = payload.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()
    return None


async def board_outline(db: AsyncSession, lesson_id: str) -> list[str]:
    """Plain-text board outline for the LLM prompt."""
    lines: list[str] = []
    for element in await load_board_elements(db, lesson_id):
        text = element_text(element)
        if not text:
            continue
        lines.append(
            f"[{element.get('element_id')}] ({element.get('type')}) {text[:600]}"
        )
    return lines
