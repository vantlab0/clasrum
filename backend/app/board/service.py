"""Board domain service: the single writer for board events and snapshots.

Authority: ``docs/CONTRACTS.md`` §2 (event envelope), §3 (payloads), §8 (DB
ownership + snapshot policy).

Invariants implemented here
---------------------------
* **One board operation == one persisted event.** Append-only history:
  ``lesson_board_events`` rows are never updated or deleted.
* **Per-lesson sequence allocation is transactional and gap-free.** On
  PostgreSQL we use ``UPDATE lessons SET last_board_sequence =
  last_board_sequence + 1 ... RETURNING last_board_sequence`` (the row lock is
  held until commit). SQLite has no ``RETURNING`` on every supported build, so
  we use a compare-and-swap loop (``UPDATE ... WHERE last_board_sequence =
  :current``) plus a bounded retry around the unique ``(lesson_id, sequence)``
  constraint as a secondary safety net.
* **Idempotent replay.** ``event_id`` is a unique idempotency key: appending the
  same ``event_id`` twice returns the first record and mutates nothing.
* **Stable element ids.** Create-events mint an id; update/target events must
  reference a live (non-removed) element of an allowed type, otherwise no
  mutation happens and a :class:`BoardValidationError` is raised.
* **Snapshot compaction.** A snapshot is written every
  ``settings.board_snapshot_interval_events`` events (and on demand). Only the
  last ``settings.board_snapshot_keep`` snapshots are kept. Reconstructing the
  board from ``latest snapshot + later events`` always equals a full replay.
* **Publishing is post-commit and best-effort.** The realtime hub is injected as
  a :class:`BoardEventPublisher` (protocol, no import of ``app.realtime``), so
  the persistence path never fails because a socket is gone.

The document reducer (:func:`apply_event_to_document`) is pure and mirrors what
the Flutter ``BoardDocument.apply`` does, which is what makes the snapshot
round-trip test meaningful.
"""
from __future__ import annotations

import asyncio
import logging
import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.board.schemas import BOARD_TOOL_EVENT_TYPES  # noqa: F401  (contract parity)
from app.config import get_settings
from app.db.constants import BoardEventSource
from app.db.models import Lesson, LessonBoardEvent, LessonBoardSnapshot

logger = logging.getLogger(__name__)

# --- event types (docs/CONTRACTS.md §3) ------------------------------------
EVENT_WRITE_TEXT = "write_text"
EVENT_WRITE_HEADING = "write_heading"
EVENT_WRITE_DEFINITION = "write_definition"
EVENT_WRITE_BULLET_LIST = "write_bullet_list"
EVENT_UPDATE_TEXT = "update_text"
EVENT_WRITE_EQUATION = "write_equation"
EVENT_UPDATE_EQUATION = "update_equation"
EVENT_HIGHLIGHT_EQUATION = "highlight_equation"
EVENT_DRAW_LINE = "draw_line"
EVENT_DRAW_SHAPE = "draw_shape"
EVENT_DRAW_PATH = "draw_path"
EVENT_DRAW_ARROW = "draw_arrow"
EVENT_DRAW_DIAGRAM = "draw_diagram"
EVENT_CREATE_COORDINATE_SYSTEM = "create_coordinate_system"
EVENT_PLOT_FUNCTION = "plot_function"
EVENT_PLOT_POINTS = "plot_points"
EVENT_ADD_GRAPH_LABEL = "add_graph_label"
EVENT_ERASE = "erase"
EVENT_ERASE_ELEMENT = "erase_element"
EVENT_HIGHLIGHT = "highlight"
EVENT_CLEAR = "clear"
EVENT_MOVE_ELEMENT = "move_element"

BOARD_EVENT_TYPES: frozenset[str] = frozenset(
    {
        EVENT_WRITE_TEXT,
        EVENT_WRITE_HEADING,
        EVENT_WRITE_DEFINITION,
        EVENT_WRITE_BULLET_LIST,
        EVENT_UPDATE_TEXT,
        EVENT_WRITE_EQUATION,
        EVENT_UPDATE_EQUATION,
        EVENT_HIGHLIGHT_EQUATION,
        EVENT_DRAW_LINE,
        EVENT_DRAW_SHAPE,
        EVENT_DRAW_PATH,
        EVENT_DRAW_ARROW,
        EVENT_DRAW_DIAGRAM,
        EVENT_CREATE_COORDINATE_SYSTEM,
        EVENT_PLOT_FUNCTION,
        EVENT_PLOT_POINTS,
        EVENT_ADD_GRAPH_LABEL,
        EVENT_ERASE,
        EVENT_ERASE_ELEMENT,
        EVENT_HIGHLIGHT,
        EVENT_CLEAR,
        EVENT_MOVE_ELEMENT,
    }
)

#: Event types that create a brand-new element (element_id is backend-minted).
NEW_ELEMENT_EVENTS: frozenset[str] = frozenset(
    {
        EVENT_WRITE_TEXT,
        EVENT_WRITE_HEADING,
        EVENT_WRITE_DEFINITION,
        EVENT_WRITE_BULLET_LIST,
        EVENT_WRITE_EQUATION,
        EVENT_DRAW_LINE,
        EVENT_DRAW_SHAPE,
        EVENT_DRAW_PATH,
        EVENT_DRAW_ARROW,
        EVENT_DRAW_DIAGRAM,
        EVENT_CREATE_COORDINATE_SYSTEM,
        EVENT_PLOT_FUNCTION,
        EVENT_PLOT_POINTS,
        EVENT_ADD_GRAPH_LABEL,
        EVENT_HIGHLIGHT,  # annotation element referencing target_ids
    }
)

#: Event types that require an existing element_id.
TARGET_ELEMENT_EVENTS: frozenset[str] = frozenset(
    {
        EVENT_UPDATE_TEXT,
        EVENT_UPDATE_EQUATION,
        EVENT_HIGHLIGHT_EQUATION,
        EVENT_ERASE_ELEMENT,
        EVENT_MOVE_ELEMENT,
    }
)

#: Event types that normally carry no element_id (``erase`` may mint one for a
#: region erase; see §3.5).
NON_ELEMENT_EVENTS: frozenset[str] = frozenset({EVENT_ERASE, EVENT_CLEAR})

#: Board layer per element type (§2: level derived from type, not sent).
ELEMENT_LEVELS: dict[str, int] = {
    EVENT_CLEAR: 1,
    EVENT_WRITE_TEXT: 2,
    EVENT_WRITE_HEADING: 2,
    EVENT_WRITE_DEFINITION: 2,
    EVENT_WRITE_BULLET_LIST: 2,
    EVENT_UPDATE_TEXT: 2,
    EVENT_MOVE_ELEMENT: 2,
    EVENT_WRITE_EQUATION: 3,
    EVENT_UPDATE_EQUATION: 3,
    EVENT_DRAW_LINE: 4,
    EVENT_DRAW_SHAPE: 4,
    EVENT_DRAW_PATH: 4,
    EVENT_DRAW_ARROW: 4,
    EVENT_DRAW_DIAGRAM: 4,
    EVENT_CREATE_COORDINATE_SYSTEM: 5,
    EVENT_PLOT_FUNCTION: 5,
    EVENT_PLOT_POINTS: 5,
    EVENT_ADD_GRAPH_LABEL: 5,
    EVENT_ERASE: 6,
    EVENT_ERASE_ELEMENT: 6,
    EVENT_HIGHLIGHT: 6,
    EVENT_HIGHLIGHT_EQUATION: 6,
}

#: Element types that count as "text-like" for update_text.
TEXT_ELEMENT_TYPES: frozenset[str] = frozenset(
    {
        EVENT_WRITE_TEXT,
        EVENT_WRITE_HEADING,
        EVENT_WRITE_DEFINITION,
        EVENT_WRITE_BULLET_LIST,
    }
)

#: Events that reference a coordinate system created earlier.
COORDINATE_SYSTEM_EVENTS: frozenset[str] = frozenset(
    {EVENT_PLOT_FUNCTION, EVENT_PLOT_POINTS, EVENT_ADD_GRAPH_LABEL}
)

MAX_SEQUENCE_ATTEMPTS = 6
MAX_RECONSTRUCTION_EVENTS = 100_000


def _rfc3339(dt: datetime) -> str:
    """RFC 3339 UTC with milliseconds, e.g. ``2026-11-11T05:50:00.123Z``."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


# ---------------------------------------------------------------------------
# publishing protocol (implemented by app.realtime.hub.LessonRealtimeHub)
# ---------------------------------------------------------------------------


@runtime_checkable
class BoardEventPublisher(Protocol):
    """Sink for committed board events.

    Implementations must be safe to call with no connected sockets and must
    never raise into the persistence path (the service still guards, because a
    buggy publisher must not roll back a committed event).
    """

    async def publish_board_event(self, lesson_id: str, event: dict[str, Any]) -> None:
        ...


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------


class BoardError(Exception):
    """Base error carrying the stable tool-result error code."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable

    def to_tool_result(self) -> dict[str, Any]:
        return {
            "ok": False,
            "error": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }


class BoardValidationError(BoardError):
    """A rejected board operation: nothing was persisted."""

    def __init__(self, code: str, message: str, *, retryable: bool = True) -> None:
        super().__init__(code, message, retryable=retryable)


class UnknownElementError(BoardValidationError):
    def __init__(self, element_id: str, expected: str | None = None) -> None:
        message = f"element id {element_id!r} does not reference an existing element"
        if expected:
            message += f" of type {expected}"
        super().__init__("unknown_element", message, retryable=True)


class UnknownLessonError(BoardError):
    def __init__(self, lesson_id: str) -> None:
        super().__init__("unknown_lesson", f"lesson {lesson_id!r} does not exist", retryable=False)


class BoardConcurrencyError(BoardError):
    def __init__(self, message: str = "could not allocate a board sequence; please retry") -> None:
        super().__init__("sequence_conflict", message, retryable=True)


# ---------------------------------------------------------------------------
# records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BoardEventRecord:
    """An append-only board event, mirroring the §2 envelope."""

    event_id: str
    session_id: str
    sequence: int
    type: str
    element_id: str | None
    payload: dict[str, Any]
    created_at: datetime
    board_revision: int
    depends_on: list[str] = field(default_factory=list)
    source: str = BoardEventSource.QWEN_TOOL
    animate: bool = True

    @classmethod
    def from_row(cls, row: LessonBoardEvent) -> "BoardEventRecord":
        return cls(
            event_id=row.event_id,
            session_id=row.lesson_id,
            sequence=row.sequence,
            type=row.type,
            element_id=row.element_id,
            payload=dict(row.payload or {}),
            created_at=row.created_at,
            board_revision=row.board_revision,
            depends_on=list(row.depends_on or []),
            source=row.source,
            animate=row.animate,
        )

    def to_envelope(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "session_id": self.session_id,
            "sequence": self.sequence,
            "type": self.type,
            "element_id": self.element_id,
            "payload": self.payload,
            "created_at": _rfc3339(self.created_at),
            "board_revision": self.board_revision,
            "depends_on": list(self.depends_on),
            "source": self.source,
            "animate": self.animate,
        }

    def to_tool_result(self) -> dict[str, Any]:
        return {
            "ok": True,
            "event_id": self.event_id,
            "sequence": self.sequence,
            "element_id": self.element_id,
        }


# ---------------------------------------------------------------------------
# pure document reducer (mirrors the Flutter BoardDocument reducer)
# ---------------------------------------------------------------------------


def empty_document(lesson_id: str) -> dict[str, Any]:
    now = _rfc3339(datetime.now(timezone.utc))
    return {
        "session_id": lesson_id,
        "revision": 0,
        "last_sequence": 0,
        "elements": [],
        "created_at": now,
        "updated_at": now,
    }


def _copy_document(document: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_id": document.get("session_id"),
        "revision": int(document.get("revision") or 0),
        "last_sequence": int(document.get("last_sequence") or 0),
        "elements": [
            {
                **element,
                "payload": dict(element.get("payload") or {}),
                "annotations": list(element.get("annotations") or []),
            }
            for element in (document.get("elements") or [])
        ],
        "created_at": document.get("created_at"),
        "updated_at": document.get("updated_at"),
    }


def _element_index(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {element["element_id"]: element for element in document.get("elements", [])}


def _element_bounds(element: dict[str, Any]) -> tuple[float, float, float, float] | None:
    position = element.get("position") or {}
    size = element.get("size") or {}
    if position.get("x") is not None and position.get("y") is not None:
        if size.get("w") is not None and size.get("h") is not None:
            return (
                float(position["x"]),
                float(position["y"]),
                float(position["x"]) + float(size["w"]),
                float(position["y"]) + float(size["h"]),
            )
        return (float(position["x"]), float(position["y"]), float(position["x"]), float(position["y"]))
    points = (element.get("payload") or {}).get("points") or []
    xs = [float(point["x"]) for point in points if isinstance(point, dict) and "x" in point]
    ys = [float(point["y"]) for point in points if isinstance(point, dict) and "y" in point]
    if xs and ys:
        return (min(xs), min(ys), max(xs), max(ys))
    return None


def _new_element(event_type: str, event: dict[str, Any]) -> dict[str, Any]:
    payload = dict(event.get("payload") or {})
    position = payload.get("position")
    size = payload.get("size")
    return {
        "element_id": event.get("element_id"),
        "type": event_type,
        "level": ELEMENT_LEVELS.get(event_type, 2),
        "payload": payload,
        "position": dict(position) if isinstance(position, dict) else None,
        "size": dict(size) if isinstance(size, dict) else None,
        "style": payload.get("style"),
        "color": payload.get("color"),
        "created_sequence": int(event["sequence"]),
        "updated_sequence": None,
        "removed_at_sequence": None,
        "animation": bool(event.get("animate", True)),
        "annotations": [],
    }


def _upsert_element(document: dict[str, Any], element: dict[str, Any]) -> None:
    index = _element_index(document)
    existing = index.get(element["element_id"])
    if existing is not None:
        existing.update(element)
    else:
        document["elements"].append(element)


def apply_event_to_document(
    document: dict[str, Any], event: dict[str, Any]
) -> dict[str, Any]:
    """Apply one board event to a document (pure; mirrors Flutter's reducer)."""
    document = _copy_document(document)
    event_type = event["type"]
    sequence = int(event["sequence"])
    element_id = event.get("element_id")
    payload = dict(event.get("payload") or {})
    index = _element_index(document)
    target = index.get(element_id) if element_id else None

    if event_type in NEW_ELEMENT_EVENTS:
        _upsert_element(document, _new_element(event_type, event))
    elif event_type == EVENT_UPDATE_TEXT:
        if target is not None:
            target["payload"].update(payload)
            for key in ("position", "size", "style", "color"):
                if payload.get(key) is not None:
                    target[key] = payload[key]
            target["updated_sequence"] = sequence
    elif event_type == EVENT_UPDATE_EQUATION:
        if target is not None:
            target["payload"].update(payload)
            if payload.get("color") is not None:
                target["color"] = payload["color"]
            target["updated_sequence"] = sequence
    elif event_type == EVENT_MOVE_ELEMENT:
        if target is not None:
            position = target.get("position") or {"x": 0.0, "y": 0.0}
            if payload.get("position"):
                new_position = dict(payload["position"])
            elif payload.get("delta"):
                new_position = {
                    "x": _clamp01(float(position.get("x", 0.0)) + float(payload["delta"]["dx"])),
                    "y": _clamp01(float(position.get("y", 0.0)) + float(payload["delta"]["dy"])),
                }
            else:  # pragma: no cover - schema guarantees one of them
                new_position = dict(position)
            target["position"] = new_position
            target["payload"]["position"] = new_position
            target["updated_sequence"] = sequence
    elif event_type == EVENT_HIGHLIGHT_EQUATION:
        if target is not None:
            target["annotations"].append(
                {
                    "kind": "highlight",
                    "style": payload.get("style", "box"),
                    "color": payload.get("color"),
                    "sequence": sequence,
                }
            )
            target["updated_sequence"] = sequence
    elif event_type == EVENT_ERASE_ELEMENT:
        if target is not None:
            target["removed_at_sequence"] = sequence
    elif event_type == EVENT_ERASE:
        if payload.get("scope") == "all":
            for element in document["elements"]:
                element["removed_at_sequence"] = element["removed_at_sequence"] or sequence
        else:
            region = payload.get("region") or {}
            rx, ry = float(region.get("x", 0)), float(region.get("y", 0))
            rw, rh = float(region.get("w", 0)), float(region.get("h", 0))
            for element in document["elements"]:
                if element.get("removed_at_sequence") is not None:
                    continue
                bounds = _element_bounds(element)
                if bounds is None:
                    continue
                if bounds[0] <= rx + rw and bounds[2] >= rx and bounds[1] <= ry + rh and bounds[3] >= ry:
                    element["removed_at_sequence"] = sequence
    elif event_type == EVENT_CLEAR:
        for element in document["elements"]:
            element["removed_at_sequence"] = element["removed_at_sequence"] or sequence
    else:  # pragma: no cover - event types are validated before persisting
        raise BoardValidationError("unknown_event_type", f"unsupported event type {event_type!r}", retryable=False)

    document["revision"] = sequence
    document["last_sequence"] = sequence
    document["updated_at"] = event.get("created_at") or _rfc3339(datetime.now(timezone.utc))
    return document


def _clamp01(value: float) -> float:
    if not math.isfinite(value):
        return 0.0
    return round(min(1.0, max(0.0, value)), 4)


def active_elements(document: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        element
        for element in document.get("elements", [])
        if element.get("removed_at_sequence") is None
    ]


# ---------------------------------------------------------------------------
# service
# ---------------------------------------------------------------------------


class BoardService:
    """Transactional board writer + snapshot manager.

    Parameters
    ----------
    publisher:
        Optional :class:`BoardEventPublisher` (the realtime hub). Publishing is
        best-effort and always happens *after* the DB commit.
    snapshot_interval / snapshot_keep:
        Override ``settings.board_snapshot_interval_events`` /
        ``settings.board_snapshot_keep`` (tests use small values).
    """

    def __init__(
        self,
        publisher: BoardEventPublisher | None = None,
        *,
        snapshot_interval: int | None = None,
        snapshot_keep: int | None = None,
    ) -> None:
        settings = get_settings()
        self._publisher = publisher
        self._snapshot_interval = max(
            1, int(snapshot_interval or settings.board_snapshot_interval_events or 40)
        )
        self._snapshot_keep = max(1, int(snapshot_keep or settings.board_snapshot_keep or 5))

    # -- publisher ---------------------------------------------------------
    def set_publisher(self, publisher: BoardEventPublisher | None) -> None:
        self._publisher = publisher

    @property
    def snapshot_interval(self) -> int:
        return self._snapshot_interval

    @property
    def snapshot_keep(self) -> int:
        return self._snapshot_keep

    async def _publish_event(self, lesson_id: str, event: BoardEventRecord) -> None:
        publisher = self._publisher
        if publisher is None:
            return
        try:
            await publisher.publish_board_event(lesson_id, event.to_envelope())
        except Exception:  # pragma: no cover - defensive: sockets must not break persistence
            logger.warning("board event publish failed lesson=%s seq=%s", lesson_id, event.sequence, exc_info=True)

    async def _publish_snapshot(self, lesson_id: str, envelope: dict[str, Any]) -> None:
        publisher = self._publisher
        notify = getattr(publisher, "publish_board_snapshot", None)
        if notify is None:
            return
        try:
            await notify(lesson_id, envelope)
        except Exception:  # pragma: no cover - defensive
            logger.warning("board snapshot publish failed lesson=%s", lesson_id, exc_info=True)

    # -- reads -------------------------------------------------------------
    async def get_lesson(self, db: AsyncSession, lesson_id: str) -> Lesson:
        lesson = await db.get(Lesson, lesson_id)
        if lesson is None:
            raise UnknownLessonError(lesson_id)
        return lesson

    async def get_event_by_event_id(
        self, db: AsyncSession, event_id: str
    ) -> BoardEventRecord | None:
        row = (
            await db.execute(
                select(LessonBoardEvent).where(LessonBoardEvent.event_id == event_id)
            )
        ).scalar_one_or_none()
        return BoardEventRecord.from_row(row) if row is not None else None

    async def get_events(
        self,
        db: AsyncSession,
        lesson_id: str,
        *,
        after_sequence: int = 0,
        limit: int | None = None,
    ) -> list[BoardEventRecord]:
        stmt = (
            select(LessonBoardEvent)
            .where(
                LessonBoardEvent.lesson_id == lesson_id,
                LessonBoardEvent.sequence > int(after_sequence),
            )
            .order_by(LessonBoardEvent.sequence)
        )
        if limit is not None:
            stmt = stmt.limit(max(1, min(int(limit), MAX_RECONSTRUCTION_EVENTS)))
        rows = (await db.execute(stmt)).scalars().all()
        return [BoardEventRecord.from_row(row) for row in rows]

    async def latest_snapshot(
        self, db: AsyncSession, lesson_id: str
    ) -> LessonBoardSnapshot | None:
        return (
            await db.execute(
                select(LessonBoardSnapshot)
                .where(LessonBoardSnapshot.lesson_id == lesson_id)
                .order_by(LessonBoardSnapshot.revision.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    async def reconstruct_document(
        self, db: AsyncSession, lesson_id: str
    ) -> dict[str, Any]:
        """Rebuild the board document from the latest snapshot + later events."""
        await self.get_lesson(db, lesson_id)
        snapshot = await self.latest_snapshot(db, lesson_id)
        if snapshot is not None and isinstance(snapshot.document, dict) and snapshot.document:
            document = _copy_document(snapshot.document)
        else:
            document = empty_document(lesson_id)
        events = await self.get_events(db, lesson_id, after_sequence=int(document.get("last_sequence") or 0))
        for record in events:
            document = apply_event_to_document(document, record.to_envelope())
        document["session_id"] = lesson_id
        return document

    async def get_snapshot_envelope(
        self,
        db: AsyncSession,
        lesson_id: str,
        *,
        include_events: bool = False,
        after_sequence: int = 0,
    ) -> dict[str, Any]:
        """Build the ``BoardSnapshotEnvelope`` DTO used by REST and WS."""
        lesson = await self.get_lesson(db, lesson_id)
        snapshot = await self.latest_snapshot(db, lesson_id)
        last_sequence = int(lesson.last_board_sequence or 0)
        if snapshot is not None:
            document: dict[str, Any] | None = _copy_document(snapshot.document)
            revision = int(snapshot.revision)
            snapshot_at = _rfc3339(snapshot.created_at)
        else:
            document = None
            revision = last_sequence
            snapshot_at = None
        envelope: dict[str, Any] = {
            "session_id": lesson_id,
            "revision": revision,
            "last_sequence": last_sequence,
            "snapshot": document,
            "snapshot_at": snapshot_at,
        }
        if include_events:
            events = await self.get_events(db, lesson_id, after_sequence=after_sequence)
            envelope["board_events"] = [record.to_envelope() for record in events]
        return envelope

    # -- sequence allocation ----------------------------------------------
    async def _allocate_sequence(self, db: AsyncSession, lesson_id: str) -> int:
        """Atomically bump ``lessons.last_board_sequence`` and return the value."""
        dialect = ""
        bind = db.bind if db.bind is not None else db.get_bind()
        if bind is not None:
            dialect = getattr(getattr(bind, "dialect", None), "name", "") or ""
        if dialect in {"postgresql", "postgres"}:
            result = await db.execute(
                update(Lesson)
                .where(Lesson.id == lesson_id)
                .values(
                    last_board_sequence=Lesson.last_board_sequence + 1,
                    board_revision=Lesson.last_board_sequence + 1,
                )
                .returning(Lesson.last_board_sequence)
            )
            new_sequence = result.scalar_one_or_none()
            if new_sequence is None:
                raise UnknownLessonError(lesson_id)
            return int(new_sequence)

        # SQLite (and any dialect without RETURNING support): compare-and-swap.
        for attempt in range(MAX_SEQUENCE_ATTEMPTS):
            current = await db.execute(
                select(Lesson.last_board_sequence).where(Lesson.id == lesson_id)
            )
            value = current.scalar_one_or_none()
            if value is None:
                raise UnknownLessonError(lesson_id)
            next_value = int(value) + 1
            result = await db.execute(
                update(Lesson)
                .where(Lesson.id == lesson_id, Lesson.last_board_sequence == int(value))
                .values(last_board_sequence=next_value, board_revision=next_value)
            )
            if getattr(result, "rowcount", 0) == 1:
                return next_value
            await asyncio.sleep(0.005 * (attempt + 1))
        raise BoardConcurrencyError("could not allocate a board sequence after retries")

    # -- append ------------------------------------------------------------
    async def append_event(
        self,
        db: AsyncSession,
        lesson_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        element_id: str | None = None,
        depends_on: list[str] | None = None,
        source: str = BoardEventSource.QWEN_TOOL,
        animate: bool = True,
        event_id: str | None = None,
        allowed_element_types: tuple[str, ...] | frozenset[str] | None = None,
    ) -> BoardEventRecord:
        """Validate, persist and publish exactly one board event.

        Raises :class:`BoardValidationError` **before** any mutation when the
        event type / target / payload is invalid, so rejected tool calls leave
        zero DB rows behind.
        """
        if event_type not in BOARD_EVENT_TYPES:
            raise BoardValidationError(
                "unknown_event_type", f"unsupported board event type {event_type!r}", retryable=False
            )
        if not isinstance(payload, dict):
            raise BoardValidationError("invalid_arguments", "payload must be an object")
        source = source if source in {s for s in (BoardEventSource.QWEN_TOOL, BoardEventSource.SYSTEM, BoardEventSource.STUDENT)} else BoardEventSource.SYSTEM

        event_id = event_id or self.new_event_id()

        # Idempotent replay: an already-persisted event_id is returned as-is.
        existing = await self.get_event_by_event_id(db, event_id)
        if existing is not None:
            return existing

        document = await self.reconstruction_for_validation(db, lesson_id)
        resolved_element_id = self._resolve_element_id(
            event_type, payload, element_id=element_id, document=document,
            allowed_element_types=allowed_element_types,
        )
        if event_type in COORDINATE_SYSTEM_EVENTS:
            self._require_coordinate_system(document, payload)
        if event_type == EVENT_HIGHLIGHT:
            self._validate_highlight_targets(document, payload)

        last_error: Exception | None = None
        for attempt in range(MAX_SEQUENCE_ATTEMPTS):
            try:
                async with db.begin_nested():
                    sequence = await self._allocate_sequence(db, lesson_id)
                    row = LessonBoardEvent(
                        lesson_id=lesson_id,
                        event_id=event_id,
                        sequence=sequence,
                        type=event_type,
                        element_id=resolved_element_id,
                        payload=payload,
                        board_revision=sequence,
                        depends_on=list(depends_on or []),
                        source=source,
                        animate=bool(animate),
                    )
                    db.add(row)
                    await db.flush()
                    await db.refresh(row)
                await db.commit()
                record = BoardEventRecord.from_row(row)
                await self._publish_event(lesson_id, record)
                await self._maybe_snapshot(db, lesson_id, record.sequence)
                return record
            except IntegrityError as exc:
                last_error = exc
                if await self.get_event_by_event_id(db, event_id) is not None:
                    replay = await self.get_event_by_event_id(db, event_id)
                    assert replay is not None
                    return replay
                await asyncio.sleep(0.01 * (attempt + 1))
        logger.error("board append failed after retries lesson=%s type=%s", lesson_id, event_type)
        raise BoardConcurrencyError() from last_error

    async def reconstruction_for_validation(
        self, db: AsyncSession, lesson_id: str
    ) -> dict[str, Any]:
        """Current document used for target validation."""
        return await self.reconstruct_document(db, lesson_id)

    def _resolve_element_id(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        element_id: str | None,
        document: dict[str, Any],
        allowed_element_types: tuple[str, ...] | frozenset[str] | None = None,
    ) -> str | None:
        if event_type in TARGET_ELEMENT_EVENTS:
            if not element_id:
                raise BoardValidationError(
                    "invalid_arguments", f"{event_type} requires element_id", retryable=True
                )
            element = self._find_live_element(document, element_id)
            allowed = set(allowed_element_types) if allowed_element_types else None
            if event_type == EVENT_UPDATE_TEXT:
                allowed = set(allowed or TEXT_ELEMENT_TYPES)
            elif event_type in {EVENT_UPDATE_EQUATION, EVENT_HIGHLIGHT_EQUATION}:
                allowed = set(allowed or {EVENT_WRITE_EQUATION})
            if allowed and element["type"] not in allowed:
                raise BoardValidationError(
                    "invalid_target",
                    f"{event_type} requires an element of type "
                    f"{', '.join(sorted(allowed))}, but {element_id!r} is {element['type']!r}",
                    retryable=True,
                )
            return element_id

        if event_type in NEW_ELEMENT_EVENTS:
            return self.new_element_id()

        if event_type == EVENT_ERASE:
            scope = payload.get("scope")
            if scope == "region":
                return self.new_element_id()
            if scope != "all":
                raise BoardValidationError(
                    "invalid_arguments", "erase.scope must be 'all' or 'region'", retryable=True
                )
            return None

        if event_type == EVENT_CLEAR:
            return None

        raise BoardValidationError(  # pragma: no cover - defensive
            "unknown_event_type", f"unsupported event type {event_type!r}", retryable=False
        )

    def _find_live_element(self, document: dict[str, Any], element_id: str) -> dict[str, Any]:
        element = _element_index(document).get(element_id)
        if element is None:
            raise UnknownElementError(element_id)
        if element.get("removed_at_sequence") is not None:
            raise BoardValidationError(
                "unknown_element",
                f"element id {element_id!r} was erased and can no longer be modified",
                retryable=True,
            )
        return element

    def _require_coordinate_system(self, document: dict[str, Any], payload: dict[str, Any]) -> None:
        coordinate_system_id = payload.get("coordinate_system_id")
        if not coordinate_system_id:
            raise BoardValidationError(
                "invalid_arguments", "coordinate_system_id is required", retryable=True
            )
        element = _element_index(document).get(str(coordinate_system_id))
        if element is None or element.get("removed_at_sequence") is not None:
            raise UnknownElementError(str(coordinate_system_id), EVENT_CREATE_COORDINATE_SYSTEM)
        if element["type"] != EVENT_CREATE_COORDINATE_SYSTEM:
            raise BoardValidationError(
                "invalid_target",
                f"{coordinate_system_id!r} is a {element['type']!r} element, not a coordinate system",
                retryable=True,
            )

    def _validate_highlight_targets(self, document: dict[str, Any], payload: dict[str, Any]) -> None:
        target_ids = payload.get("target_ids") or []
        if not target_ids:
            raise BoardValidationError(
                "invalid_arguments", "highlight.target_ids must contain at least one id", retryable=True
            )
        for target_id in target_ids:
            self._find_live_element(document, str(target_id))

    @staticmethod
    def new_event_id() -> str:
        return "evt_" + uuid.uuid4().hex[:24]

    @staticmethod
    def new_element_id() -> str:
        return "el_" + uuid.uuid4().hex[:24]

    # -- snapshots ---------------------------------------------------------
    async def _maybe_snapshot(self, db: AsyncSession, lesson_id: str, sequence: int) -> None:
        if self._snapshot_interval <= 0 or sequence % self._snapshot_interval != 0:
            return
        try:
            await self.create_snapshot(db, lesson_id)
        except Exception:  # pragma: no cover - snapshotting must never fail a tool call
            logger.warning("board snapshot failed lesson=%s seq=%s", lesson_id, sequence, exc_info=True)

    async def create_snapshot(
        self, db: AsyncSession, lesson_id: str, *, force: bool = False
    ) -> dict[str, Any]:
        """Persist (or reuse) a snapshot for the current board revision."""
        lesson = await self.get_lesson(db, lesson_id)
        last_sequence = int(lesson.last_board_sequence or 0)
        existing = await self.latest_snapshot(db, lesson_id)
        if existing is not None and int(existing.revision) == last_sequence and not force:
            return self._snapshot_envelope(existing)
        if existing is not None and int(existing.revision) == last_sequence and force:
            return self._snapshot_envelope(existing)

        document = await self.reconstruct_document(db, lesson_id)
        row = LessonBoardSnapshot(
            lesson_id=lesson_id,
            revision=int(document.get("last_sequence") or last_sequence),
            last_sequence=int(document.get("last_sequence") or last_sequence),
            document=document,
        )
        try:
            async with db.begin_nested():
                db.add(row)
                await db.flush()
        except IntegrityError:
            # Another writer created the snapshot for this revision first.
            await db.rollback()
            raced = await self.latest_snapshot(db, lesson_id)
            if raced is not None:
                return self._snapshot_envelope(raced)
            raise
        await db.commit()
        await self._prune_snapshots(db, lesson_id)
        envelope = self._snapshot_envelope(row)
        await self._publish_snapshot(lesson_id, envelope)
        return envelope

    async def _prune_snapshots(self, db: AsyncSession, lesson_id: str) -> None:
        rows = (
            await db.execute(
                select(LessonBoardSnapshot.id)
                .where(LessonBoardSnapshot.lesson_id == lesson_id)
                .order_by(LessonBoardSnapshot.revision.desc())
            )
        ).scalars().all()
        stale = list(rows)[self._snapshot_keep :]
        if not stale:
            return
        await db.execute(delete(LessonBoardSnapshot).where(LessonBoardSnapshot.id.in_(stale)))
        await db.commit()

    @staticmethod
    def _snapshot_envelope(row: LessonBoardSnapshot) -> dict[str, Any]:
        return {
            "session_id": row.lesson_id,
            "revision": int(row.revision),
            "last_sequence": int(row.last_sequence),
            "snapshot": _copy_document(row.document),
            "snapshot_at": _rfc3339(row.created_at),
        }

    async def compact(self, db: AsyncSession, lesson_id: str) -> dict[str, Any]:
        """Force a snapshot (session end / explicit ``board.request_snapshot``)."""
        return await self.create_snapshot(db, lesson_id, force=True)


__all__ = [
    "BOARD_EVENT_TYPES",
    "BoardConcurrencyError",
    "BoardError",
    "BoardEventPublisher",
    "BoardEventRecord",
    "BoardService",
    "BoardValidationError",
    "COORDINATE_SYSTEM_EVENTS",
    "ELEMENT_LEVELS",
    "NEW_ELEMENT_EVENTS",
    "NON_ELEMENT_EVENTS",
    "TARGET_ELEMENT_EVENTS",
    "TEXT_ELEMENT_TYPES",
    "UnknownElementError",
    "UnknownLessonError",
    "active_elements",
    "apply_event_to_document",
    "empty_document",
]
