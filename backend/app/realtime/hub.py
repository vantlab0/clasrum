"""Realtime lesson WebSocket hub.

Authority: ``docs/CONTRACTS.md`` §6 (Realtime WebSocket protocol).

Responsibilities
----------------
* One socket per active lesson; a ``lease_id`` per socket. A newer socket for the
  same lesson supersedes the old one: the old socket receives
  ``session.interrupted`` then close code ``4001``.
* Authenticates via ``?token=<jwt>`` query param (browsers cannot set WS headers).
* Resolves ownership server-side; the client never supplies a student id.
* Bridges Qwen Realtime (audio + tool calls) <-> Flutter client (audio + board
  + teaching events).
* Implements :class:`app.board.service.BoardEventPublisher` and
  :class:`app.board.service.LessonStatePublisher` so the board registry and
  graph service can publish into the live socket.
* Best-effort publishing: a disconnected or superseded socket must never roll
  back a committed board event or fail a tool call.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from fastapi import WebSocket, WebSocketDisconnect, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.board.service import (
    EVENT_DRAW_PLAN,
    BoardEventPublisher,
    BoardService,
    BoardValidationError,
)
from app.board.registry import LessonStatePublisher
from app.config import get_settings
from app.contracts import dtos, mappers
from app.core import errors
from app.core.deps import resolve_student
from app.db.constants import BoardEventSource, Speaker
from app.db.models import Lesson
from app.db.session import session_scope
from app.graph import service as graph_service
from app.lessons import service as lessons_service
from app.lessons.board_reads import get_board_snapshot, get_transcript_cursor
from app.lessons.transcripts import append_transcript_entry
from app.realtime.qwen_adapter import build_turn_detection
from app.realtime.transcript_triggers import TranscriptTriggerSystem

logger = logging.getLogger(__name__)

# Close codes (docs/CONTRACTS.md §6).
CLOSE_SUPERSEDED = 4001  # newer socket took the lease
CLOSE_FORBIDDEN = 4003  # not the lesson owner
CLOSE_LESSON_ENDED = 4004
CLOSE_QWEN_UNAVAILABLE = 4010

# Guard rails for automatic teaching continuation. Student speech resets the
# counter; a silent/empty model turn cannot spin forever.
MAX_AUTO_CONTINUES_PER_TURN = 120
AUTO_CONTINUE_COOLDOWN_SECONDS = 1.0

# A short grace window lets a function call arrive before buffered speech is
# released to the client. Pure-speech responses are flushed immediately on
# response.done (or when this timer expires), so low latency is preserved.
AUDIO_TOOL_GRACE_SECONDS = 0.15
# All response.create requests go through ``_request_response``; it blocks
# overlapping responses and rapid duplicate requests from different paths.
RESPONSE_REQUEST_COOLDOWN_SECONDS = 0.5
CONTINUATION_WATCHDOG_SECONDS = 5.0

# WS message types (docs/CONTRACTS.md §6).
MSG_SESSION_READY = "session.ready"
MSG_BOARD_EVENT = "board.event"
MSG_BOARD_EVENTS = "board.events"
MSG_BOARD_SNAPSHOT = "board.snapshot"
MSG_AUDIO_DELTA = "audio.delta"
MSG_AUDIO_DONE = "audio.done"
MSG_AUDIO_TRUNCATED = "audio.truncated"
MSG_TEACHER_STATE = "teacher.state"
MSG_TRANSCRIPT_ENTRY = "transcript.entry"
MSG_LESSON_STATE = "lesson.state"
MSG_SESSION_INTERRUPTED = "session.interrupted"
MSG_USER_SPEECH_STARTED = "user.speech_started"  # legacy VAD notification only
MSG_MIC_UNLOCK = "mic.unlock"  # tutor asked a direct question needing an answer
MSG_MIC_LOCK = "mic.lock"  # answer turn finished/cancelled
MSG_SESSION_ENDED = "session.ended"
MSG_ERROR = "error"
MSG_PONG = "pong"

CLIENT_AUDIO_START = "audio.start"
CLIENT_AUDIO_STOP = "audio.stop"
CLIENT_SESSION_INTERRUPT = "session.interrupt"
CLIENT_SESSION_END = "session.end"
CLIENT_BOARD_REQUEST_SNAPSHOT = "board.request_snapshot"
CLIENT_BOARD_BACKFILL = "board.backfill"
CLIENT_BOARD_VIEWPORT_FULL = "board.viewport_full"
CLIENT_BOARD_CLEAR = "board.clear"
CLIENT_PING = "ping"


@runtime_checkable
class QwenSession(Protocol):
    """A Qwen Realtime transport over the real DashScope WebSocket."""

    async def connect(self, instructions: str, tools: list[dict[str, Any]]) -> None:
        ...

    async def append_audio(self, payload_b64: str) -> None:
        ...

    async def commit_audio(self) -> None:
        ...

    async def create_response(self) -> None:
        ...

    async def cancel_response(self) -> None:
        ...

    async def function_call_output(
        self, call_id: str, output: str, *, continue_response: bool = False
    ) -> None:
        ...

    async def prompt_text(self, text: str) -> None:
        """Send a text instruction; the hub owns response.create requests."""
        ...

    async def iterate(self) -> AsyncIterator[dict[str, Any]]:
        """Yield server-side Qwen events (audio deltas, transcripts, tool calls, ...)."""

    async def aclose(self) -> None:
        ...


class LessonStartupPhase(str, Enum):
    IDLE = "idle"
    PROMPT_SENT = "prompt_sent"
    RESPONSE_STARTED = "response_started"
    AUDIO_STARTED = "audio_started"
    TOOL_CALL_RECEIVED = "tool_call_received"
    BOARD_DIRECTOR_STARTED = "board_director_started"
    BOARD_EVENT_PERSISTED = "board_event_persisted"
    READY = "ready"
    FAILED = "failed"


@dataclass
class ResponseLifecycle:
    response_id: str
    created_at: float
    completed_at: float | None = None
    status: str | None = None
    has_audio: bool = False
    has_text: bool = False
    has_function_call: bool = False
    function_call_executed_ok: bool | None = None


@dataclass
class ToolCallState:
    """One observed Qwen function call inside a single response."""

    call_id: str
    name: str = ""
    sequence: int | None = None
    submitted: bool = False
    settled: bool = False
    ok: bool | None = None
    replay: bool = False


@dataclass
class ResponseGate:
    """Orders/gates one response's audio against its function calls.

    Audio deltas are buffered while function calls observed so far are still
    executing (or have failed). Pure-speech responses use a short bounded grace
    timer so they play promptly. Deltas are deduped and sorted by their Qwen
    sequence before being forwarded; the original sequence value is preserved.
    """

    response_id: str
    buffer: list[tuple[int, int, dict[str, Any]]] = field(default_factory=list)
    seen_sequences: set[int] = field(default_factory=set)
    next_local_sequence: int = 0
    last_sequence: int | None = None
    last_tool_sequence: int | None = None
    gap_detected: bool = False
    has_board_call: bool = False
    released: bool = False
    discarded: bool = False
    finalized: bool = False
    grace_task: asyncio.Task[None] | None = None
    audio_done_event: dict[str, Any] | None = None
    done_event: dict[str, Any] | None = None
    calls: dict[str, ToolCallState] = field(default_factory=dict)

    def has_pending_calls(self) -> bool:
        return any(not call.settled for call in self.calls.values())

    def has_unresolved_audio(self) -> bool:
        return bool(self.buffer) or self.has_pending_calls()


@dataclass(eq=False)
class LessonSocket:
    """One live client socket for a lesson.

    ``eq=False`` keeps identity equality/hash semantics: sockets are stored in
    sets inside the hub, and the dataclass must stay hashable.
    """

    websocket: WebSocket
    lesson_id: str
    student_id: str
    lease_id: str
    applied_sequence: int = 0
    qwen: QwenSession | None = None
    qwen_task: asyncio.Task[None] | None = None
    closed: asyncio.Event = field(default_factory=asyncio.Event)
    initial_teaching_started: bool = False
    last_board_event_at: float = 0.0
    last_qwen_event_at: float = 0.0
    last_qwen_response_created_at: float = 0.0
    last_qwen_audio_at: float = 0.0
    last_qwen_transcript_at: float = 0.0
    last_tool_call_at: float = 0.0
    last_board_director_started_at: float = 0.0
    last_board_director_finished_at: float = 0.0
    last_board_director_error: str | None = None
    startup_phase: LessonStartupPhase = LessonStartupPhase.IDLE
    startup_attempt: int = 0
    startup_nudge_count: int = 0
    startup_response_ids: set[str] = field(default_factory=set)
    startup_has_board_tool_call: bool = False
    startup_has_board_event: bool = False
    # A fresh lesson is not ready until a concept diagram has been committed.
    startup_has_visual_event: bool = False
    last_draw_plan_event_at: float = 0.0
    startup_last_nudge_at: float = 0.0
    startup_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    response_lifecycles: dict[str, ResponseLifecycle] = field(default_factory=dict)
    active_response_id: str | None = None
    # Teaching continues automatically after each response unless the tutor
    # explicitly asks for the student's answer via wait_for_student (or finishes).
    waiting_for_student: bool = False
    # Manual-turn microphone gate. The client microphone stays locked until a
    # wait_for_student tool call unlocks it for one answer turn.
    mic_unlocked: bool = False
    mic_request_id: str | None = None
    audio_turn_active: bool = False
    lesson_finished: bool = False
    auto_continue_count: int = 0
    last_auto_continue_at: float = 0.0
    # Replayed function_call_arguments.done events must never execute a tool
    # twice. Cache the first result per (response_id, call_id) and resend it.
    tool_call_results: dict[str, dict[str, Any]] = field(default_factory=dict)
    tool_call_locks: dict[str, asyncio.Lock] = field(default_factory=dict)
    # Managed tool tasks are keyed by response_id:call_id so the Qwen event loop
    # never awaits a slow tool inline and replays cannot execute twice.
    tool_tasks: dict[str, asyncio.Task[None]] = field(default_factory=dict)
    response_gates: dict[str, ResponseGate] = field(default_factory=dict)
    response_request_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # Set while create_response() is in flight: response.created may not have
    # arrived yet, so active_response_id alone cannot prevent duplicate creates.
    response_request_pending: bool = False
    last_response_request_at: float = 0.0
    audio_gap_count: int = 0
    tool_gap_count: int = 0
    # Visible-board recycling: the client reports measured fullness; the server
    # owns the persisted wipe so snapshots/replay/Qwen context stay in sync.
    last_viewport_report_sequence: int = -1
    last_viewport_cleared_sequence: int = -1
    last_manual_clear_sequence: int = -1
    viewport_clear_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class LessonRealtimeHub:
    """Manages all live lesson sockets and bridges them to Qwen.

    The hub is a singleton-ish object created once per FastAPI process and
    injected into the board service / registry as the publish sink.
    """

    def __init__(self) -> None:
        # lesson_id -> active LessonSocket (the lease holder)
        self._sockets: dict[str, LessonSocket] = {}
        # lesson_id -> set of all sockets (including superseded, pending close)
        self._all: dict[str, set[LessonSocket]] = {}
        self._lock = asyncio.Lock()
        self._board: BoardService | None = None
        self._qwen_factory: type[QwenSession] | None = None
        self._trigger_system: TranscriptTriggerSystem | None = None
        # Lessons that have successfully produced initial board content.
        self._startup_completed_lessons: set[str] = set()
        # Per-lesson startup lock to prevent duplicate startup across reconnects.
        self._startup_locks: dict[str, asyncio.Lock] = {}
        # Lease id currently attempting startup for a lesson (if any).
        self._startup_active_lease: dict[str, str] = {}

    # -- wiring -----------------------------------------------------------

    def set_board_service(self, board: BoardService) -> None:
        self._board = board
        try:
            board.set_publisher(self)
        except Exception:  # pragma: no cover - defensive wiring
            logger.warning("failed to wire board publisher", exc_info=True)

    def set_qwen_factory(self, factory: type[QwenSession]) -> None:
        self._qwen_factory = factory

    def set_trigger_system(self, trigger_system: TranscriptTriggerSystem) -> None:
        """Attach the transcript trigger system (shared with the Board Director)."""
        self._trigger_system = trigger_system

    @staticmethod
    def _startup_board_ready(socket: LessonSocket) -> bool:
        """Startup is complete only after the first concept diagram exists."""
        return socket.startup_has_visual_event or socket.last_draw_plan_event_at > 0

    @property
    def trigger_system(self) -> TranscriptTriggerSystem | None:
        return self._trigger_system

    # -- BoardEventPublisher / LessonStatePublisher ----------------------

    async def publish_board_event(self, lesson_id: str, event: dict[str, Any]) -> None:
        """Publish a committed board event to the live socket (best-effort)."""
        socket = await self._active_socket(lesson_id)
        if socket is None:
            logger.info("board event publish skipped (no socket) lesson=%s seq=%s", lesson_id, event.get("sequence"))
            return  # no live client; event is still persisted
        try:
            await socket.websocket.send_json(
                {MSG_TYPE: MSG_BOARD_EVENT, "event": event}
            )
            socket.last_board_event_at = time.monotonic()
            socket.startup_has_board_event = True
            socket.startup_phase = LessonStartupPhase.BOARD_EVENT_PERSISTED
            socket.last_board_director_error = None
            event_type = event.get("type") if isinstance(event, dict) else None
            if event_type == EVENT_DRAW_PLAN:
                socket.startup_has_visual_event = True
                socket.last_draw_plan_event_at = socket.last_board_event_at
            if socket.last_board_director_finished_at == 0.0 and socket.last_board_director_started_at > 0:
                socket.last_board_director_finished_at = time.monotonic()
            async with self._lock:
                if self._startup_board_ready(socket):
                    self._startup_completed_lessons.add(lesson_id)
                    if self._startup_active_lease.get(lesson_id) == socket.lease_id:
                        self._startup_active_lease.pop(lesson_id, None)
            logger.info(
                "board event broadcast lesson=%s seq=%s type=%s event_id=%s",
                lesson_id,
                event.get("sequence"),
                event_type,
                event.get("event_id"),
            )
        except Exception:  # pragma: no cover - a dead socket must not roll back
            logger.warning(
                "board event publish failed (socket gone) lesson=%s seq=%s",
                lesson_id,
                event.get("sequence"),
                exc_info=True,
            )

    async def publish_lesson_state(
        self, lesson_id: str, lesson_state: dict[str, Any]
    ) -> None:
        socket = await self._active_socket(lesson_id)
        if socket is None:
            return
        try:
            detail = await _lesson_detail(lesson_id)
            await socket.websocket.send_json(
                {MSG_TYPE: MSG_LESSON_STATE, "lesson": detail}
            )
        except Exception:  # pragma: no cover
            logger.warning("lesson state publish failed lesson=%s", lesson_id, exc_info=True)

    async def publish_lesson_event(
        self,
        lesson_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        socket = await self._active_socket(lesson_id)
        if socket is None:
            return
        try:
            await socket.websocket.send_json(
                {MSG_TYPE: MSG_LESSON_STATE, "event": event_type, "payload": payload}
            )
        except Exception:  # pragma: no cover
            logger.warning("lesson event publish failed lesson=%s", lesson_id, exc_info=True)

    # -- connection lifecycle --------------------------------------------

    async def connect(
        self,
        websocket: WebSocket,
        lesson_id: str,
        token: str | None,
    ) -> None:
        """Accept a client WS, authenticate, resolve ownership, open Qwen."""
        await websocket.accept()
        socket = LessonSocket(
            websocket=websocket,
            lesson_id=lesson_id,
            student_id="",  # filled after auth
            lease_id=uuid.uuid4().hex,
        )

        # Authenticate + ownership (REST-less path; token comes from query param).
        async with session_scope() as db:
            try:
                student = await resolve_student(db, token)
            except errors.ApiError as exc:
                await _send_close(socket, exc.status_code, CLOSE_FORBIDDEN, exc.message)
                return
            lesson = await db.get(Lesson, lesson_id)
            if lesson is None:
                await _send_close(socket, 404, CLOSE_FORBIDDEN, "Lesson not found")
                return
            if lesson.student_id != student.id:
                await _send_close(socket, 403, CLOSE_FORBIDDEN, "Lesson belongs to another student")
                return
            socket.student_id = student.id

        # Supersede any existing lease holder for this lesson.
        async with self._lock:
            old = self._sockets.get(lesson_id)
            if old is not None and old is not socket:
                try:
                    await old.websocket.send_json(
                        {
                            MSG_TYPE: MSG_SESSION_INTERRUPTED,
                            "reason": "superseded",
                            "at_sequence": old.applied_sequence,
                        }
                    )
                    await old.websocket.close(code=CLOSE_SUPERSEDED)
                except Exception:  # pragma: no cover - best effort
                    logger.warning("failed to close superseded socket lesson=%s", lesson_id, exc_info=True)
                await self._cancel_pending_work(old, reason="superseded")
                old.closed.set()
            self._sockets[lesson_id] = socket
            self._all.setdefault(lesson_id, set()).add(socket)

        try:
            await websocket.send_json(
                {
                    MSG_TYPE: "connected",
                    "lease_id": socket.lease_id,
                    "lesson_id": lesson_id,
                }
            )

            # Load lesson detail + board snapshot + transcript cursor for session.ready.
            async with session_scope() as db:
                lesson = await db.get(Lesson, lesson_id)
                assert lesson is not None
                detail = _detail_dict(await lessons_service.build_lesson_detail(db, lesson))
                board_envelope = await get_board_snapshot(db, lesson, include_events=False)
                board_events = await BoardService().get_events(db, lesson.id)
                cursor = await get_transcript_cursor(db, lesson.id)

            # A resumed lesson with an existing diagram is already prepared.
            socket.startup_has_visual_event = any(
                getattr(event, "type", None) == EVENT_DRAW_PLAN
                for event in board_events
            )

            # Open the real Qwen Realtime transport. No offline fallback exists.
            settings = get_settings()
            if self._qwen_factory is None:
                raise RuntimeError("Qwen Realtime transport is not configured")
            qwen = self._qwen_factory()
            socket.qwen = qwen
            try:
                await asyncio.wait_for(
                    qwen.connect(
                        instructions=_teacher_instructions(detail),
                        tools=_openai_tools(),
                    ),
                    timeout=max(1.0, float(settings.qwen_connect_timeout_seconds)),
                )
                socket.last_qwen_event_at = time.monotonic()
                logger.info(
                    "qwen session established lesson=%s lease=%s model=%s",
                    lesson_id,
                    socket.lease_id,
                    settings.qwen_model,
                )
            except Exception as exc:
                logger.exception("Qwen realtime connect failed lesson=%s", lesson_id)
                async with self._lock:
                    self._sockets.pop(lesson_id, None)
                    self._all.get(lesson_id, set()).discard(socket)
                actual = f"{type(exc).__name__}: {exc}".strip()
                if len(actual) > 240:
                    actual = actual[:237] + "..."
                message = (
                    "Could not connect to the Qwen model. "
                    f"{actual}"
                )
                await _send_error(
                    socket,
                    "qwen_unavailable",
                    message,
                    retryable=True,
                )
                try:
                    await socket.websocket.close(code=CLOSE_QWEN_UNAVAILABLE)
                except Exception:  # pragma: no cover - best effort
                    pass
                return

            # Send session.ready per §6.
            await socket.websocket.send_json(
                {
                    MSG_TYPE: MSG_SESSION_READY,
                    "lesson_id": lesson_id,
                    "student_id": student.id,
                    "lease_id": socket.lease_id,
                    "board": _jsonable(board_envelope),
                    "transcript_cursor": _jsonable(cursor),
                    "lesson": detail,
                    "audio": {
                        "input_sample_rate": settings.qwen_input_sample_rate,
                        "output_sample_rate": settings.qwen_output_sample_rate,
                        "format": "pcm16",
                        "channels": 1,
                        "turn_detection": (
                            "manual"
                            if build_turn_detection(
                                settings.qwen_turn_detection,
                                settings.qwen_vad_silence_ms,
                            )
                            is None
                            else settings.qwen_turn_detection
                        ),
                        "mic_locked": True,
                    },
                }
            )
            logger.info(
                "session.ready sent lesson=%s lease=%s last_sequence=%s transcript_cursor=%s",
                lesson_id,
                socket.lease_id,
                (board_envelope.get("last_sequence") if isinstance(board_envelope, dict) else None),
                cursor,
            )

            # Bridge Qwen -> client in the background.
            socket.qwen_task = asyncio.create_task(self._qwen_loop(socket, lesson_id))

            # Kick off initial teaching for fresh lessons (no duplicate on resume).
            try:
                await self._maybe_start_initial_teaching(socket, lesson, detail, board_envelope, cursor)
            except Exception:  # pragma: no cover - must not kill the session
                logger.exception("initial teaching start failed lesson=%s", lesson_id)
            self._schedule_startup_watchdog(socket, lesson_id)

            # Client -> Qwen loop.
            await self._client_loop(socket, lesson_id)
        except WebSocketDisconnect:
            pass
        finally:
            await self.disconnect(socket)

    async def disconnect(self, socket: LessonSocket) -> None:
        await self._lock_answer_mic(socket, reason="disconnected")
        await self._cancel_pending_work(socket, reason="disconnected")
        if socket.qwen_task is not None:
            socket.qwen_task.cancel()
        if socket.qwen is not None:
            try:
                await socket.qwen.aclose()
            except Exception:  # pragma: no cover
                logger.warning("qwen close failed lesson=%s", socket.lesson_id, exc_info=True)
        socket.closed.set()
        async with self._lock:
            self._all.setdefault(socket.lesson_id, set()).discard(socket)
            if self._sockets.get(socket.lesson_id) is socket:
                self._sockets.pop(socket.lesson_id, None)
            # If this socket owned startup and startup never completed, allow retry.
            if self._startup_active_lease.get(socket.lesson_id) == socket.lease_id:
                if (
                    socket.lesson_id not in self._startup_completed_lessons
                    and not self._startup_board_ready(socket)
                ):
                    self._startup_active_lease.pop(socket.lesson_id, None)

    async def _lock_answer_mic(self, socket: LessonSocket, *, reason: str) -> None:
        """End an answer window and tell the client to close its microphone."""
        if not socket.mic_unlocked and not socket.audio_turn_active:
            return
        request_id = socket.mic_request_id
        socket.mic_unlocked = False
        socket.mic_request_id = None
        socket.audio_turn_active = False
        try:
            await socket.websocket.send_json(
                {
                    MSG_TYPE: MSG_MIC_LOCK,
                    "lesson_id": socket.lesson_id,
                    "request_id": request_id,
                    "reason": reason,
                }
            )
        except Exception:  # pragma: no cover - transport teardown
            logger.debug("mic.lock send failed lesson=%s", socket.lesson_id, exc_info=True)

    async def _persist_system_board_clear(
        self,
        socket: LessonSocket,
        lesson_id: str,
        sequence: int,
        *,
        scope: str = "all",
        region: str | None = None,
    ) -> bool:
        """Persist one system-owned board clear and broadcast it.

        The clear is intentionally owned by the server so snapshots, replay and
        the tutor's context stay in sync. Returns False when the client report
        is stale or the operation was rejected.
        """
        try:
            async with session_scope() as db:
                lesson = await db.get(Lesson, lesson_id)
                current_sequence = int(
                    getattr(lesson, "last_board_sequence", 0) or 0
                )
            if sequence < current_sequence:
                # The client is looking at an older board; let it catch up.
                return False
            from app.board.registry import ToolRegistry

            board = self._board or BoardService()
            # Make the publisher explicit even when this hub has no shared board
            # service wired (test harnesses), so the committed clear reaches the
            # live socket and the client can recycle the same viewport.
            board.set_publisher(self)
            registry = ToolRegistry(
                board=board,
                trigger_system=self._trigger_system,
            )
            registry.set_lesson_publisher(self)
            args: dict[str, Any] = {"scope": scope}
            if scope == "region":
                args["region"] = region
            async with session_scope() as db:
                result = await registry.execute_structured(
                    db,
                    lesson_id,
                    "board_clear_region",
                    args,
                    source=BoardEventSource.SYSTEM,
                )
        except Exception:  # pragma: no cover - never break the socket
            logger.exception("board clear failed lesson=%s scope=%s region=%s", lesson_id, scope, region)
            await _send_error(socket, "internal_error", "could not clear the board")
            return False
        if not result.get("ok"):
            logger.warning("board clear rejected lesson=%s result=%s", lesson_id, result)
            return False
        return True

    async def _recycle_desktop_board(
        self,
        socket: LessonSocket,
        lesson_id: str,
        sequence: int,
    ) -> tuple[bool, str]:
        """Desktop fullness policy: fill left then right; when right is full clear left first."""
        try:
            async with session_scope() as db:
                lesson = await db.get(Lesson, lesson_id)
                if lesson is None:
                    return False, "none"
                board = self._board or BoardService()
                document = await board.reconstruct_document(db, lesson_id)
                elements = [
                    el
                    for el in (document.get("elements") or [])
                    if isinstance(el, dict) and el.get("removed_at_sequence") is None
                ]
        except Exception:  # pragma: no cover
            logger.exception("desktop recycle inspection failed lesson=%s", lesson_id)
            return False, "none"

        left = 0
        right = 0
        for element in elements:
            if not isinstance(element, dict):
                continue
            payload = element.get("payload") if isinstance(element.get("payload"), dict) else {}
            pos = element.get("position") or payload.get("position") or {}
            x = pos.get("x")
            try:
                x_val = float(x)
            except Exception:
                continue
            if x_val < 0.5:
                left += 1
            else:
                right += 1

        # Requested behavior: after right side is full, clean left first and write there.
        if right > 0 and left > 0:
            ok = await self._persist_system_board_clear(
                socket, lesson_id, sequence, scope="region", region="left"
            )
            return ok, "left"
        if left > 0 and right == 0:
            ok = await self._persist_system_board_clear(
                socket, lesson_id, sequence, scope="region", region="right"
            )
            return ok, "right"
        ok = await self._persist_system_board_clear(socket, lesson_id, sequence, scope="all")
        return ok, "all"

    async def _nudge_after_clear(self, socket: LessonSocket, message: str) -> None:
        if (
            socket.qwen is not None
            and not socket.waiting_for_student
            and not socket.lesson_finished
        ):
            await self._prompt_text(
                socket, message, reason="viewport_recycle_nudge"
            )

    @staticmethod
    def _is_narrow_layout_mode(layout_mode: str) -> bool:
        """True for mobile/narrow/compact/portrait client layouts.

        The Flutter client currently sends ``mobile``/``desktop``; accept the
        other narrow aliases so a future compact/portrait report cannot
        accidentally trigger a destructive desktop-style wipe.
        """
        value = str(layout_mode or "").strip().lower()
        tokens = {part for part in value.replace("-", "_").replace(" ", "_").split("_") if part}
        narrow_tokens = {"mobile", "narrow", "compact", "portrait", "phone", "small"}
        if tokens & narrow_tokens:
            return True
        return value.startswith("mobile") or value.startswith("narrow") or value.startswith("compact")

    async def _handle_viewport_full(
        self,
        socket: LessonSocket,
        lesson_id: str,
        envelope: dict[str, Any],
    ) -> None:
        """Record narrow-board fullness; recycle the desktop board once."""
        try:
            sequence = int(envelope.get("sequence") or 0)
            coverage = float(envelope.get("coverage") or 0.0)
            scale = float(envelope.get("scale") or 1.0)
        except (TypeError, ValueError):
            await _send_error(socket, "invalid_arguments", "invalid viewport_full report")
            return
        if sequence <= 0 or not 0.0 <= coverage <= 1.0 or not 0.5 <= scale <= 2.0:
            await _send_error(socket, "invalid_arguments", "viewport_full report out of range")
            return
        if sequence <= socket.last_viewport_cleared_sequence:
            return  # latch already released this exact board state
        if sequence <= socket.last_viewport_report_sequence:
            return  # duplicate/older report

        layout_mode = str(envelope.get("layout_mode") or "").strip().lower()
        narrow = self._is_narrow_layout_mode(layout_mode)

        if narrow:
            # Mobile boards have a virtual vertical scroll extent: reaching the
            # bottom is a reading-progress signal, not a request to wipe the
            # board. Record/report fullness and stop; recycling is desktop-only.
            logger.info(
                "mobile viewport_bottom recorded lesson=%s sequence=%s coverage=%.2f mode=%s (no clear)",
                lesson_id, sequence, coverage, layout_mode or "unknown",
            )
            socket.last_viewport_report_sequence = sequence
            return

        async with socket.viewport_clear_lock:
            if sequence <= socket.last_viewport_cleared_sequence:
                return
            if sequence <= socket.last_viewport_report_sequence:
                return
            socket.last_viewport_report_sequence = sequence
            ok, clear_kind = await self._recycle_desktop_board(socket, lesson_id, sequence)
            if not ok:
                return
            socket.last_viewport_cleared_sequence = sequence
            logger.info(
                "visible board full -> recycled lesson=%s sequence=%s coverage=%.2f mode=%s kind=%s",
                lesson_id,
                sequence,
                coverage,
                layout_mode or "unknown",
                clear_kind,
            )
            if clear_kind == "left":
                message = (
                    "System instruction: The right side of the board is full. The left side was cleared; "
                    "continue teaching on the left side. Write one short line per board_write_on_board call "
                    "with actual teaching content, not meta labels like 'Definition' or 'Intuition'."
                )
            elif clear_kind == "right":
                message = (
                    "System instruction: The left side of the board is full. The right side was cleared; "
                    "continue teaching on the right side. Write one short line per board_write_on_board call "
                    "with actual teaching content, not meta labels like 'Definition' or 'Intuition'."
                )
            else:
                message = (
                    "System instruction: The student's visible board was full and "
                    "has been wiped. The board is empty now; continue teaching "
                    "from the cleared board with one short line per board_write_on_board call. "
                    "Write actual teaching content, not meta labels like 'Definition' or 'Intuition'."
                )
            await self._nudge_after_clear(socket, message)

    async def _handle_board_clear(
        self,
        socket: LessonSocket,
        lesson_id: str,
        envelope: dict[str, Any],
    ) -> None:
        """Explicit student-initiated clear. Idempotent per board sequence."""
        try:
            sequence = int(envelope.get("sequence") or 0)
        except (TypeError, ValueError):
            await _send_error(socket, "invalid_arguments", "invalid board.clear request")
            return
        if sequence <= 0:
            await _send_error(socket, "invalid_arguments", "board.clear needs a sequence")
            return
        if sequence <= socket.last_manual_clear_sequence:
            return
        async with socket.viewport_clear_lock:
            if sequence <= socket.last_manual_clear_sequence:
                return
            if sequence <= socket.last_viewport_cleared_sequence:
                return
            socket.last_manual_clear_sequence = sequence
            if not await self._persist_system_board_clear(socket, lesson_id, sequence):
                return
            socket.last_viewport_cleared_sequence = sequence
            logger.info("student board.clear accepted lesson=%s sequence=%s", lesson_id, sequence)

    # -- loops -----------------------------------------------------------

    async def _client_loop(
        self,
        socket: LessonSocket,
        lesson_id: str,
    ) -> None:
        """Read client messages: audio frames + control messages."""
        assert socket.qwen is not None
        while not socket.closed.is_set():
            try:
                message = await socket.websocket.receive()
            except WebSocketDisconnect:
                break
            except Exception:  # pragma: no cover - transport error
                logger.warning("client receive failed lesson=%s", lesson_id, exc_info=True)
                break

            if message.get("type") == "websocket.receive":
                text = message.get("text")
                bytes_data = message.get("bytes")
                if bytes_data is not None:
                    # Manual turns only: never forward mic audio before the
                    # tutor has explicitly unlocked it for an answer.
                    if not socket.mic_unlocked or not socket.audio_turn_active:
                        continue
                    # Raw PCM16 mono 16 kHz -> Qwen input_audio_buffer.append.
                    import base64

                    b64 = base64.b64encode(bytes_data).decode("ascii")
                    try:
                        await socket.qwen.append_audio(b64)
                    except Exception:  # pragma: no cover
                        logger.warning("qwen append_audio failed lesson=%s", lesson_id, exc_info=True)
                    continue
                if text is not None:
                    await self._handle_client_text(socket, lesson_id, text)
                    continue

        # End of client stream: signal Qwen we're done sending audio for this turn.
        # Do not restart a finished lesson or override an explicit wait-for-student.
        if socket.qwen is not None:
            await self._request_response(socket, reason="client_stream_end")

    async def _handle_client_text(
        self,
        socket: LessonSocket,
        lesson_id: str,
        text: str,
    ) -> None:
        try:
            envelope = json.loads(text)
        except json.JSONDecodeError:
            await _send_error(socket, "invalid_json", "malformed WS message")
            return
        msg_type = str(envelope.get("type") or "")

        if msg_type == CLIENT_PING:
            await socket.websocket.send_json({MSG_TYPE: MSG_PONG, "t": envelope.get("t")})
            return

        if msg_type == CLIENT_AUDIO_START:
            # Only an unlocked answer window may open the audio path. The
            # waiting_for_student flag stays set until a submitted answer ends
            # the turn, so an audio.start cannot silently resume teaching.
            reason = str(envelope.get("reason") or "")
            request_id = str(envelope.get("request_id") or "")
            if reason != "answer" or not socket.mic_unlocked:
                logger.debug("ignored locked audio.start lesson=%s", lesson_id)
                return
            if (
                socket.mic_request_id
                and request_id
                and request_id != socket.mic_request_id
            ):
                logger.debug("ignored stale audio.start lesson=%s", lesson_id)
                return
            socket.audio_turn_active = True
            logger.debug("answer audio.start lesson=%s request=%s", lesson_id, request_id)
            return

        if msg_type == CLIENT_AUDIO_STOP:
            request_id = str(envelope.get("request_id") or "")
            if (
                socket.mic_request_id
                and request_id
                and request_id != socket.mic_request_id
            ):
                return
            submitted = envelope.get("submitted") is not False
            if not submitted:
                # Aborted answer: drop the uncommitted audio but keep the
                # question and microphone window open so the student can retry.
                socket.audio_turn_active = False
                try:
                    if socket.qwen is not None:
                        clear_audio = getattr(socket.qwen, "clear_audio", None)
                        if clear_audio is not None:
                            await clear_audio()
                except Exception:  # pragma: no cover - transport best effort
                    logger.debug("qwen clear_audio failed lesson=%s", lesson_id, exc_info=True)
                return
            await self._lock_answer_mic(socket, reason="answer_submitted")
            socket.waiting_for_student = False
            socket.auto_continue_count = 0
            try:
                if socket.qwen is not None:
                    try:
                        await socket.qwen.commit_audio()
                    except Exception:  # pragma: no cover - commit may be unsupported in some modes
                        logger.debug("qwen commit_audio failed lesson=%s", lesson_id, exc_info=True)
                    await self._request_response(
                        socket, reason="answer_submitted", force=True
                    )
            except Exception:  # pragma: no cover
                logger.warning("qwen create_response failed lesson=%s", lesson_id, exc_info=True)
            return

        if msg_type == CLIENT_SESSION_INTERRUPT:
            # Barge-in is only accepted from an explicitly unlocked answer turn.
            was_answer_window = socket.mic_unlocked or socket.audio_turn_active
            await self._lock_answer_mic(socket, reason="interrupted")
            if not was_answer_window:
                return
            socket.active_response_id = None
            socket.waiting_for_student = False
            socket.auto_continue_count = 0
            try:
                if socket.qwen is not None:
                    await socket.qwen.cancel_response()
            except Exception:  # pragma: no cover
                logger.warning("qwen cancel_response failed lesson=%s", lesson_id, exc_info=True)
            await self._cancel_pending_work(socket, reason="interrupted")
            return

        if msg_type == CLIENT_SESSION_END:
            reason = str(envelope.get("reason") or "student")
            await self._end_lesson(socket, lesson_id, reason)
            return

        if msg_type == CLIENT_BOARD_REQUEST_SNAPSHOT:
            try:
                async with session_scope() as fresh_db:
                    lesson = await fresh_db.get(Lesson, lesson_id)
                    assert lesson is not None
                    envelope_board = await lessons_service.get_board_snapshot(
                        fresh_db, lesson, include_events=False
                    )
                await socket.websocket.send_json(
                    {MSG_TYPE: MSG_BOARD_SNAPSHOT, "board": _jsonable(envelope_board)}
                )
            except Exception as exc:  # pragma: no cover
                logger.exception("snapshot request failed lesson=%s", lesson_id)
                await _send_error(socket, "internal_error", "could not produce snapshot")
            return

        if msg_type == CLIENT_BOARD_VIEWPORT_FULL:
            await self._handle_viewport_full(socket, lesson_id, envelope)
            return

        if msg_type == CLIENT_BOARD_CLEAR:
            await self._handle_board_clear(socket, lesson_id, envelope)
            return

        if msg_type == CLIENT_BOARD_BACKFILL:
            after_sequence = int(envelope.get("after_sequence") or 0)
            try:
                async with session_scope() as fresh_db:
                    events = await lessons_service.board_events(
                        fresh_db, lesson_id, after_sequence=after_sequence
                    )
                await socket.websocket.send_json(
                    {
                        MSG_TYPE: MSG_BOARD_EVENTS,
                        "events": _jsonable(events),
                        "from_sequence": after_sequence + 1,
                        "to_sequence": after_sequence + len(events),
                    }
                )
            except Exception as exc:  # pragma: no cover
                logger.exception("backfill failed lesson=%s", lesson_id)
                await _send_error(socket, "internal_error", "could not backfill events")
            return

        await _send_error(socket, "invalid_arguments", f"unknown client message type {msg_type!r}")

    async def _qwen_loop(
        self,
        socket: LessonSocket,
        lesson_id: str,
    ) -> None:
        """Bridge Qwen events -> client while tool calls run in managed tasks."""
        assert socket.qwen is not None
        audio_delta_count = 0
        try:
            async for event in socket.qwen.iterate():
                if socket.closed.is_set():
                    break
                socket.last_qwen_event_at = time.monotonic()
                etype = str(event.get("type") or "")

                if etype == "error":
                    err = event.get("error") if isinstance(event.get("error"), dict) else {}
                    code = err.get("code") or event.get("code") or "qwen_error"
                    msg = err.get("message") or event.get("message") or "Qwen error"
                    resp_id = err.get("response_id") or event.get("response_id")
                    logger.warning("qwen error lesson=%s code=%s response_id=%s message=%s", lesson_id, code, resp_id, msg)
                    if resp_id and resp_id in socket.response_lifecycles:
                        socket.response_lifecycles[resp_id].status = "failed"
                    await _send_error(socket, "qwen_unavailable", str(msg), retryable=True)
                    continue

                if etype == "input_audio_buffer.speech_started":
                    # Legacy VAD notification only. Manual turns do not use this to
                    # unlock the microphone or end a wait_for_student pause.
                    await socket.websocket.send_json(
                        {MSG_TYPE: MSG_USER_SPEECH_STARTED, "source": "qwen"}
                    )
                    continue

                if etype == "response.created":
                    response = event.get("response") if isinstance(event.get("response"), dict) else {}
                    response_id = str(response.get("id") or event.get("response_id") or "")
                    socket.last_qwen_response_created_at = time.monotonic()
                    socket.active_response_id = response_id or socket.active_response_id
                    if response_id:
                        socket.response_lifecycles[response_id] = ResponseLifecycle(response_id=response_id, created_at=time.monotonic(), status="created")
                        self._response_gate(socket, response_id)
                        socket.startup_response_ids.add(response_id)
                        if socket.startup_phase in {LessonStartupPhase.IDLE, LessonStartupPhase.PROMPT_SENT}:
                            socket.startup_phase = LessonStartupPhase.RESPONSE_STARTED
                    logger.info("qwen response.created lesson=%s response_id=%s", lesson_id, response_id)
                    continue

                if etype == "response.output_item.added":
                    item = event.get("item") if isinstance(event.get("item"), dict) else {}
                    response_id = str(event.get("response_id") or socket.active_response_id or "")
                    item_type = str(item.get("type") or "")
                    logger.info("qwen output_item.added lesson=%s response_id=%s item_type=%s", lesson_id, response_id, item_type)
                    if response_id and response_id in socket.response_lifecycles and item_type == "function_call":
                        socket.response_lifecycles[response_id].has_function_call = True
                        gate = self._response_gate(socket, response_id)
                        call_id = str(item.get("call_id") or "")
                        if gate is not None and call_id:
                            gate.calls.setdefault(call_id, ToolCallState(call_id=call_id))
                            if gate.grace_task is not None:
                                gate.grace_task.cancel()
                                gate.grace_task = None
                            gate.released = False
                    continue

                if etype in {"response.audio.delta", "response.output_audio.delta"}:
                    audio_delta_count += 1
                    socket.last_qwen_audio_at = time.monotonic()
                    response_id = str(event.get("response_id") or socket.active_response_id or "")
                    if response_id and response_id in socket.response_lifecycles:
                        socket.response_lifecycles[response_id].has_audio = True
                    if socket.startup_phase in {LessonStartupPhase.PROMPT_SENT, LessonStartupPhase.RESPONSE_STARTED}:
                        socket.startup_phase = LessonStartupPhase.AUDIO_STARTED
                        await self._send_startup_status(socket, "audio_started", "Preparing your first lesson...")
                    if audio_delta_count == 1 or audio_delta_count % 50 == 0:
                        logger.info("qwen audio.delta lesson=%s count=%s response_id=%s type=%s", lesson_id, audio_delta_count, response_id, etype)
                    await self._handle_audio_delta(socket, lesson_id, event, response_id)
                    continue

                if etype in {"response.audio.done", "response.output_audio.done"}:
                    response_id = str(event.get("response_id") or socket.active_response_id or "")
                    logger.info("qwen audio.done lesson=%s response_id=%s type=%s", lesson_id, response_id, etype)
                    gate = self._response_gate(socket, response_id)
                    if gate is not None and not gate.discarded:
                        if gate.has_unresolved_audio():
                            # Never let audio.done overtake buffered deltas.
                            gate.audio_done_event = event
                        else:
                            await socket.websocket.send_json(
                                {
                                    MSG_TYPE: MSG_AUDIO_DONE,
                                    "response_id": event.get("response_id") or response_id,
                                }
                            )
                    continue

                if etype in {"response.cancelled", "response.cancel"}:
                    cancel_id = str(event.get("response_id") or socket.active_response_id or "")
                    await self._cancel_buffered_audio(
                        socket, response_id=cancel_id or None, reason="response_cancelled"
                    )
                    continue

                if etype in {
                    "conversation.item.input_audio_transcription.completed",
                    "response.audio_transcript.delta",
                    "response.audio_transcript.done",
                    "response.output_audio_transcript.delta",
                    "response.output_audio_transcript.done",
                }:
                    socket.last_qwen_transcript_at = time.monotonic()
                    response_id = str(event.get("response_id") or socket.active_response_id or "")
                    if response_id and response_id in socket.response_lifecycles:
                        socket.response_lifecycles[response_id].has_text = True
                    entry = self._transcript_entry_from_qwen(lesson_id, event)
                    if entry is not None:
                        if str(entry.get("speaker")) == "student":
                            await self._lock_answer_mic(socket, reason="answer_transcribed")
                            socket.waiting_for_student = False
                            socket.auto_continue_count = 0
                        logger.info(
                            "qwen transcript lesson=%s speaker=%s final=%s chars=%s response_id=%s",
                            lesson_id,
                            entry.get("speaker"),
                            etype in {"conversation.item.input_audio_transcription.completed", "response.audio_transcript.done", "response.output_audio_transcript.done"},
                            len(str(entry.get("text") or "")),
                            response_id,
                        )
                        await self._persist_transcript(lesson_id, entry)
                        await socket.websocket.send_json({MSG_TYPE: MSG_TRANSCRIPT_ENTRY, "entry": entry})
                        if self._trigger_system is not None:
                            try:
                                is_final = etype in {
                                    "conversation.item.input_audio_transcription.completed",
                                    "response.audio_transcript.done",
                                    "response.output_audio_transcript.done",
                                }
                                async with session_scope() as db:
                                    await self._trigger_system.handle_transcript(
                                        db,
                                        lesson_id,
                                        str(entry.get("text") or ""),
                                        speaker=str(entry.get("speaker") or Speaker.TUTOR),
                                        is_final=is_final,
                                    )
                            except Exception:  # pragma: no cover
                                logger.exception("transcript trigger processing failed lesson=%s", lesson_id)
                    continue

                if etype == "response.function_call_arguments.delta":
                    response_id = str(event.get("response_id") or socket.active_response_id or "")
                    call_id = str(event.get("call_id") or "")
                    name = str(event.get("name") or "")
                    logger.debug("qwen function_call_arguments.delta lesson=%s response_id=%s call_id=%s name=%s", lesson_id, response_id, call_id, name)
                    if response_id and call_id:
                        gate = self._response_gate(socket, response_id)
                        if gate is not None:
                            state = gate.calls.setdefault(call_id, ToolCallState(call_id=call_id))
                            state.name = name or state.name
                            # Observing a call (even before arguments.done) gates
                            # audio; the event loop keeps consuming Qwen events.
                            if gate.grace_task is not None:
                                gate.grace_task.cancel()
                                gate.grace_task = None
                            gate.released = False
                    continue

                if etype == "response.function_call_arguments.done":
                    response_id = str(event.get("response_id") or socket.active_response_id or "")
                    if response_id and response_id in socket.response_lifecycles:
                        socket.response_lifecycles[response_id].has_function_call = True
                    tool_name = str(event.get("name") or "")
                    gate = self._response_gate(socket, response_id)
                    if gate is not None and tool_name in {"board_intent", "create_board_visual"}:
                        gate.has_board_call = True
                    call_id = str(event.get("call_id") or "")
                    if response_id and call_id:
                        await self._submit_tool_call(
                            socket, lesson_id, event, response_id, call_id
                        )
                    continue

                if etype == "response.done":
                    response = event.get("response") if isinstance(event.get("response"), dict) else {}
                    response_id = str(response.get("id") or event.get("response_id") or socket.active_response_id or "")
                    status = str(response.get("status") or "")
                    gate = self._response_gate(socket, response_id)
                    if gate is not None:
                        # A call may still be executing in its managed task. Hold
                        # response.done (and the client's audio.done) until the gate
                        # resolves; the event loop itself never blocks.
                        gate.done_event = event
                        for call_id, state in list(gate.calls.items()):
                            if state.submitted:
                                continue
                            logger.warning(
                                "function call had no arguments.done lesson=%s response_id=%s call_id=%s",
                                lesson_id, response_id, call_id,
                            )
                            state.settled = True
                    if status in {"failed", "cancelled"}:
                        await self._cancel_buffered_audio(
                            socket, response_id=response_id or None,
                            reason=f"response_{status}",
                        )
                    if gate is None:
                        await self._finalize_response(socket, event)
                    elif gate.discarded:
                        if gate.done_event is not None:
                            gate.done_event = None
                            await self._finalize_response(socket, event)
                    elif not gate.has_pending_calls():
                        # No tools left: response.done proves no more calls can
                        # arrive, so release buffered speech immediately.
                        await self._flush_audio_buffer(socket, gate, reason="response_done")
                    continue

                if etype == "teacher.state":
                    await socket.websocket.send_json(
                        {MSG_TYPE: MSG_TEACHER_STATE, **event.get("payload", {})}
                    )
                    continue

                # Unknown Qwen events are ignored (forwards compatibility).
        finally:
            # Give already-submitted tools a chance to settle when the Qwen
            # stream ends, so buffered audio is either flushed or explicitly
            # discarded. Cancellation (disconnect/supersede) is handled by
            # _cancel_pending_work instead.
            if not socket.closed.is_set():
                pending = [task for task in socket.tool_tasks.values() if not task.done()]
                if pending:
                    try:
                        await asyncio.wait_for(
                            asyncio.gather(*pending, return_exceptions=True),
                            timeout=30.0,
                        )
                    except (asyncio.TimeoutError, asyncio.CancelledError):  # pragma: no cover
                        pending_ids = {id(task) for task in pending}
                        for task in pending:
                            if not task.done():
                                task.cancel()
                        for key, task in list(socket.tool_tasks.items()):
                            if id(task) in pending_ids:
                                await self._abandon_tool_task(
                                    socket, key, reason="stream_end_tool_timeout"
                                )

    async def _handle_qwen_tool_call(
        self,
        socket: LessonSocket,
        lesson_id: str,
        event: dict[str, Any],
        response_id: str | None = None,
    ) -> None:
        """Execute a tool call at most once, even if Qwen replays the event."""
        call_id = str(event.get("call_id") or "")
        if not call_id:
            return
        key = f"{response_id or event.get('response_id') or '-'}:{call_id}"
        lock = socket.tool_call_locks.setdefault(key, asyncio.Lock())
        async with lock:
            cached = socket.tool_call_results.get(key)
            if cached is not None:
                logger.info(
                    "qwen function_call replay ignored lesson=%s call_id=%s response_id=%s",
                    lesson_id, call_id, response_id,
                )
                try:
                    if socket.qwen is not None:
                        # Replays only resend the cached, already persisted
                        # result. Never request a second response here: the
                        # original execution already owns that continuation.
                        await socket.qwen.function_call_output(
                            call_id, json.dumps(cached)
                        )
                except Exception:  # pragma: no cover - replay ack must not kill the socket
                    logger.warning("replayed function_call_output failed lesson=%s", lesson_id, exc_info=True)
                return
            result = await self._execute_qwen_tool_call(socket, lesson_id, event, response_id=response_id)
            if result is not None:
                socket.tool_call_results[key] = result
                if len(socket.tool_call_results) > 256:
                    stale = list(socket.tool_call_results)[:-256]
                    for old_key in stale:
                        socket.tool_call_results.pop(old_key, None)
                        socket.tool_call_locks.pop(old_key, None)

    async def _execute_qwen_tool_call(
        self,
        socket: LessonSocket,
        lesson_id: str,
        event: dict[str, Any],
        response_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Execute a Qwen tool call via the registry and return the result."""
        import time as _time

        from app.board.registry import ToolRegistry

        call_id = event.get("call_id")
        name = str(event.get("name") or "")
        arguments = event.get("arguments", "{}")
        if not call_id or not name:
            return
        socket.last_tool_call_at = _time.monotonic()
        response_id = str(response_id or event.get("response_id") or socket.active_response_id or "")
        if response_id and response_id in socket.response_lifecycles:
            socket.response_lifecycles[response_id].has_function_call = True
        logger.info("qwen function_call lesson=%s name=%s call_id=%s response_id=%s", lesson_id, name, call_id, response_id)

        if name == "wait_for_student":
            # Explicit signal: only now does the tutor stop and listen. Rhetorical
            # questions and routine checks must not pause automatic teaching or
            # grant microphone access.
            try:
                parsed_args = json.loads(arguments) if isinstance(arguments, str) else dict(arguments or {})
            except (TypeError, ValueError):
                parsed_args = {}
            reason = str(parsed_args.get("reason") or "").strip()
            if not reason:
                reason = "The tutor asked a question that needs your answer."
            socket.waiting_for_student = True
            socket.auto_continue_count = 0
            socket.mic_unlocked = True
            socket.mic_request_id = call_id
            socket.audio_turn_active = False
            result = {
                "ok": True,
                "waiting_for_student": True,
                "mic_unlocked": True,
                "request_id": call_id,
            }
            logger.info("wait_for_student accepted lesson=%s response_id=%s", lesson_id, response_id)
            try:
                await socket.websocket.send_json(
                    {
                        MSG_TYPE: MSG_MIC_UNLOCK,
                        "lesson_id": lesson_id,
                        "request_id": call_id,
                        "reason": reason,
                        "requires_answer": True,
                        "turn_detection": "manual",
                    }
                )
            except Exception:  # pragma: no cover - transport teardown
                logger.warning("mic.unlock send failed lesson=%s", lesson_id, exc_info=True)
            if socket.qwen is not None:
                try:
                    await socket.qwen.function_call_output(
                        call_id,
                        json.dumps(result),
                        continue_response=False,
                    )
                except Exception:  # pragma: no cover
                    logger.warning("wait_for_student output failed lesson=%s", lesson_id, exc_info=True)
            return result

        if name == "lesson_finish":
            socket.lesson_finished = True
            await self._lock_answer_mic(socket, reason="lesson_finished")
            await self._cancel_buffered_audio(
                socket, response_id=response_id or None, reason="lesson_finished"
            )

        if name == "board_intent":
            socket.startup_has_board_tool_call = True
            socket.startup_phase = LessonStartupPhase.TOOL_CALL_RECEIVED
            socket.last_board_director_started_at = _time.monotonic()
            socket.last_board_director_finished_at = 0.0
            socket.last_board_director_error = None
            await self._send_startup_status(socket, "board_intent", "Creating the first board illustration...")
            logger.info("board_intent tool_call received lesson=%s call_id=%s response_id=%s", lesson_id, call_id, response_id)

        registry = ToolRegistry(
            board=self._board or BoardService(),
            trigger_system=self._trigger_system,
        )
        registry.set_lesson_publisher(self)
        started = _time.perf_counter()
        try:
            async with session_scope() as db:
                result = await registry.execute_structured(
                    db, lesson_id, name, arguments, source="qwen_tool"
                )
        except BoardValidationError as exc:
            result = exc.to_tool_result()
        except Exception:  # pragma: no cover - registry must not kill the socket
            logger.exception("tool call failed lesson=%s tool=%s", lesson_id, name)
            result = {
                "ok": False,
                "error": "internal_error",
                "message": "the board operation could not be completed",
                "retryable": False,
            }
        finally:
            ok = bool(result.get("ok")) if isinstance(result, dict) else False
            if response_id and response_id in socket.response_lifecycles:
                lifecycle = socket.response_lifecycles[response_id]
                # One failed call must not be overwritten by a later success:
                # continuation is only safe when every call persisted cleanly.
                lifecycle.function_call_executed_ok = (
                    lifecycle.function_call_executed_ok is not False and ok
                )
            if name in {"board_intent", "create_board_visual"}:
                socket.last_board_director_finished_at = _time.monotonic()
                if not ok:
                    socket.last_board_director_error = str((result or {}).get("message") or (result or {}).get("error") or "board_visual_failed")
                else:
                    socket.last_board_director_error = None
                    socket.startup_has_board_event = True
                    socket.startup_has_visual_event = True
                    socket.last_draw_plan_event_at = _time.monotonic()
                    socket.startup_phase = LessonStartupPhase.BOARD_EVENT_PERSISTED
                    if socket.last_board_event_at == 0:
                        socket.last_board_event_at = socket.last_draw_plan_event_at
                    async with self._lock:
                        self._startup_completed_lessons.add(lesson_id)
                        if self._startup_active_lease.get(lesson_id) == socket.lease_id:
                            self._startup_active_lease.pop(lesson_id, None)
                logger.info(
                    "board_intent handled lesson=%s ok=%s duration_ms=%.1f error=%s seq=%s",
                    lesson_id,
                    ok,
                    (_time.perf_counter() - started) * 1000,
                    (result.get("error") if isinstance(result, dict) and not ok else None),
                    (result.get("sequence") if isinstance(result, dict) else None),
                )

        if isinstance(result, dict) and not result.get("ok"):
            logger.warning(
                "tool call failed lesson=%s tool=%s call_id=%s code=%s retryable=%s message=%s",
                lesson_id,
                name,
                call_id,
                result.get("error"),
                result.get("retryable"),
                str(result.get("message") or "")[:240],
            )
            if name == "board_intent":
                # Allow controlled recovery: a failed board_intent does not satisfy startup.
                socket.startup_has_board_tool_call = False
                socket.startup_phase = LessonStartupPhase.RESPONSE_STARTED if (socket.last_qwen_audio_at or socket.last_qwen_transcript_at) else LessonStartupPhase.PROMPT_SENT
                try:
                    await socket.websocket.send_json(
                        {
                            MSG_TYPE: "startup.status",
                            "phase": "board_intent_failed",
                            "message": "Preparing your first lesson...",
                            "error": str(result.get("message") or "Board drawing failed"),
                            "code": str(result.get("error") or "board_intent_failed"),
                            "retryable": bool(result.get("retryable", True)),
                        }
                    )
                except Exception:  # pragma: no cover
                    pass

        try:
            if socket.qwen is not None:
                await socket.qwen.function_call_output(call_id, json.dumps(result))
        except Exception:  # pragma: no cover
            logger.warning("qwen function_call_output failed lesson=%s", lesson_id, exc_info=True)
        return result

    # -- response gating / central response requests ----------------------

    @staticmethod
    def _tool_key(response_id: str, call_id: str) -> str:
        return f"{response_id}:{call_id}"

    def _response_gate(
        self, socket: LessonSocket, response_id: str, *, create: bool = True
    ) -> ResponseGate | None:
        if not response_id:
            return None
        gate = socket.response_gates.get(response_id)
        if gate is None and create:
            gate = ResponseGate(response_id=response_id)
            socket.response_gates[response_id] = gate
            # Bound memory across long lessons without touching pending work.
            if len(socket.response_gates) > 128:
                for old_id, old_gate in list(socket.response_gates.items()):
                    if old_id != response_id and not old_gate.has_unresolved_audio():
                        socket.response_gates.pop(old_id, None)
                        if len(socket.response_gates) <= 128:
                            break
        return gate

    async def _request_response(
        self, socket: LessonSocket, *, reason: str, force: bool = False
    ) -> bool:
        """Single guarded entry point for every ``response.create`` request.

        ``force`` only bypasses the short cooldown; active responses, wait-for-
        student pauses and finished/closed sockets are always respected.
        """
        if socket.closed.is_set() or socket.qwen is None:
            logger.info("response.create skipped reason=%s lesson=%s (socket closed)", reason, socket.lesson_id)
            return False
        if socket.waiting_for_student or socket.lesson_finished:
            logger.info(
                "response.create skipped reason=%s lesson=%s waiting=%s finished=%s",
                reason, socket.lesson_id, socket.waiting_for_student, socket.lesson_finished,
            )
            return False
        now = time.monotonic()
        async with socket.response_request_lock:
            if socket.active_response_id is not None or socket.response_request_pending:
                logger.info(
                    "response.create skipped reason=%s lesson=%s active_response=%s pending=%s",
                    reason,
                    socket.lesson_id,
                    socket.active_response_id,
                    socket.response_request_pending,
                )
                return False
            if not force:
                since_request = now - socket.last_response_request_at
                since_created = now - socket.last_qwen_response_created_at
                if (
                    since_request < RESPONSE_REQUEST_COOLDOWN_SECONDS
                    or since_created < RESPONSE_REQUEST_COOLDOWN_SECONDS
                ):
                    logger.info(
                        "response.create skipped reason=%s lesson=%s cooldown request=%.3f created=%.3f",
                        reason, socket.lesson_id, since_request, since_created,
                    )
                    return False
            socket.last_response_request_at = now
            socket.response_request_pending = True
        # Do not hold the lock across the transport round trip: other callers
        # must be able to see the pending guard and skip immediately.
        try:
            await socket.qwen.create_response()
            logger.info("response.create requested reason=%s lesson=%s", reason, socket.lesson_id)
            return True
        except Exception:  # pragma: no cover - transport best effort
            logger.warning("response.create failed reason=%s lesson=%s", reason, socket.lesson_id, exc_info=True)
            return False
        finally:
            async with socket.response_request_lock:
                socket.response_request_pending = False

    async def _prompt_text(
        self,
        socket: LessonSocket,
        text: str,
        *,
        reason: str,
        request_response: bool = True,
    ) -> bool:
        """Send an instruction; request the follow-up response through the guard."""
        if socket.closed.is_set() or socket.qwen is None:
            return False
        try:
            await socket.qwen.prompt_text(text)
        except Exception:  # pragma: no cover - transport best effort
            logger.warning("prompt_text failed reason=%s lesson=%s", reason, socket.lesson_id, exc_info=True)
            return False
        if request_response:
            # prompt_text replaces adapter-side create_response: the hub decides
            # whether a new turn is safe, but the prompt itself is a new turn so
            # the short inter-response cooldown must not suppress it.
            await self._request_response(socket, reason=reason, force=True)
        return True

    def _schedule_audio_grace(
        self, socket: LessonSocket, gate: ResponseGate
    ) -> None:
        if gate.grace_task is not None or gate.calls or gate.released or gate.discarded:
            return
        gate.grace_task = asyncio.create_task(
            self._audio_grace_timer(socket, gate),
            name=f"audio-grace-{socket.lesson_id}-{gate.response_id}",
        )

    async def _audio_grace_timer(
        self, socket: LessonSocket, gate: ResponseGate
    ) -> None:
        try:
            await asyncio.sleep(AUDIO_TOOL_GRACE_SECONDS)
            if gate.discarded or gate.released or gate.calls:
                return
            await self._flush_audio_buffer(socket, gate, reason="no_call_grace")
        except asyncio.CancelledError:
            return
        except Exception:  # pragma: no cover - never break the bridge
            logger.warning("audio grace flush failed lesson=%s response=%s", socket.lesson_id, gate.response_id, exc_info=True)
        finally:
            gate.grace_task = None

    async def _handle_audio_delta(
        self,
        socket: LessonSocket,
        lesson_id: str,
        event: dict[str, Any],
        response_id: str,
    ) -> None:
        gate = self._response_gate(socket, response_id)
        if gate is None or gate.discarded:
            return
        raw_sequence = event.get("sequence")
        if isinstance(raw_sequence, int) and not isinstance(raw_sequence, bool):
            source_sequence: int | None = raw_sequence
            order_sequence = raw_sequence
        else:
            source_sequence = None
            order_sequence = 1_000_000 + gate.next_local_sequence
            gate.next_local_sequence += 1
        if order_sequence in gate.seen_sequences:
            logger.info(
                "qwen audio.delta duplicate ignored lesson=%s response_id=%s sequence=%s",
                lesson_id, response_id, raw_sequence,
            )
            return
        gate.seen_sequences.add(order_sequence)
        if source_sequence is not None:
            if gate.last_sequence is not None and source_sequence > gate.last_sequence + 1:
                gate.gap_detected = True
                socket.audio_gap_count += 1
                logger.warning(
                    "qwen audio.delta gap lesson=%s response_id=%s expected=%s got=%s",
                    lesson_id, response_id, gate.last_sequence + 1, source_sequence,
                )
            gate.last_sequence = (
                source_sequence
                if gate.last_sequence is None
                else max(gate.last_sequence, source_sequence)
            )
        payload = {
            MSG_TYPE: MSG_AUDIO_DELTA,
            "data": event.get("delta", ""),
            "sample_rate": get_settings().qwen_output_sample_rate,
            "sequence": event.get("sequence"),
        }
        if gate.released and not gate.calls:
            await socket.websocket.send_json(payload)
            return
        gate.buffer.append((order_sequence, len(gate.buffer), payload))
        if not gate.calls and not gate.released:
            self._schedule_audio_grace(socket, gate)

    async def _flush_audio_buffer(
        self, socket: LessonSocket, gate: ResponseGate, *, reason: str
    ) -> None:
        if gate.discarded:
            return
        if gate.grace_task is not None:
            gate.grace_task.cancel()
            gate.grace_task = None
        if gate.buffer:
            entries = sorted(gate.buffer, key=lambda item: (item[0], item[1]))
            gate.buffer.clear()
            gate.released = True
            logger.info(
                "audio gate flushed lesson=%s response_id=%s deltas=%s reason=%s",
                socket.lesson_id, gate.response_id, len(entries), reason,
            )
            for _, _, payload in entries:
                await socket.websocket.send_json(payload)
        else:
            gate.released = True
        if gate.audio_done_event is not None:
            audio_done = gate.audio_done_event
            gate.audio_done_event = None
            await socket.websocket.send_json(
                {
                    MSG_TYPE: MSG_AUDIO_DONE,
                    "response_id": audio_done.get("response_id") or gate.response_id,
                }
            )
        if gate.done_event is not None and not gate.finalized:
            done_event = gate.done_event
            gate.done_event = None
            await self._finalize_response(socket, done_event)

    async def _cancel_buffered_audio(
        self,
        socket: LessonSocket,
        *,
        response_id: str | None = None,
        reason: str,
    ) -> None:
        """Discard buffered speech so no false confirmation can be spoken."""
        gates: list[ResponseGate]
        if response_id:
            gate = self._response_gate(socket, response_id, create=False)
            gates = [gate] if gate is not None else []
        else:
            gates = list(socket.response_gates.values())
        for gate in gates:
            if gate.discarded:
                continue
            had_audio = bool(gate.buffer) or not gate.released
            if gate.grace_task is not None:
                gate.grace_task.cancel()
                gate.grace_task = None
            gate.buffer.clear()
            gate.discarded = True
            gate.audio_done_event = None
            # Keep done_event: a deferred response.done still needs to clear
            # active_response_id so it cannot block the next teaching turn.
            if had_audio:
                logger.info(
                    "buffered audio discarded lesson=%s response_id=%s reason=%s",
                    socket.lesson_id, gate.response_id, reason,
                )
                try:
                    await socket.websocket.send_json(
                        {
                            MSG_TYPE: MSG_AUDIO_TRUNCATED,
                            "response_id": gate.response_id,
                            "reason": reason,
                        }
                    )
                except Exception:  # pragma: no cover - teardown
                    logger.debug("audio.truncated send failed lesson=%s", socket.lesson_id, exc_info=True)

    async def _abandon_tool_task(
        self, socket: LessonSocket, key: str, *, reason: str
    ) -> None:
        """Settle a cancelled tool so its response gate cannot block forever."""
        response_id, _, call_id = key.partition(":")
        if not response_id or not call_id:
            return
        gate = self._response_gate(socket, response_id, create=False)
        if gate is not None:
            state = gate.calls.setdefault(call_id, ToolCallState(call_id=call_id))
            state.settled = True
            state.ok = False
        lifecycle = socket.response_lifecycles.get(response_id)
        if lifecycle is not None:
            lifecycle.function_call_executed_ok = False
        await self._cancel_buffered_audio(
            socket, response_id=response_id, reason=reason
        )
        if gate is not None and gate.done_event is not None:
            done_event = gate.done_event
            gate.done_event = None
            await self._finalize_response(socket, done_event)
        elif socket.active_response_id == response_id:
            # No response.done ever arrived; do not leave the turn guard latched.
            socket.active_response_id = None

    async def _cancel_pending_work(
        self, socket: LessonSocket, *, reason: str
    ) -> None:
        await self._cancel_buffered_audio(socket, reason=reason)
        for key, task in list(socket.tool_tasks.items()):
            if not task.done():
                task.cancel()
            await self._abandon_tool_task(socket, key, reason=reason)
        socket.tool_tasks.clear()

    async def _submit_tool_call(
        self,
        socket: LessonSocket,
        lesson_id: str,
        event: dict[str, Any],
        response_id: str,
        call_id: str,
    ) -> None:
        """Submit tool execution to a managed task; never block the event loop."""
        gate = self._response_gate(socket, response_id)
        if gate is None or gate.discarded:
            return
        state = gate.calls.setdefault(call_id, ToolCallState(call_id=call_id))
        state.name = str(event.get("name") or state.name)
        raw_sequence = event.get("sequence")
        if isinstance(raw_sequence, int) and not isinstance(raw_sequence, bool):
            state.sequence = raw_sequence
            if gate.last_tool_sequence is not None and raw_sequence > gate.last_tool_sequence + 1:
                socket.tool_gap_count += 1
                logger.warning(
                    "qwen function_call sequence gap lesson=%s response_id=%s call_id=%s expected=%s got=%s",
                    lesson_id, response_id, call_id, gate.last_tool_sequence + 1, raw_sequence,
                )
            gate.last_tool_sequence = (
                raw_sequence
                if gate.last_tool_sequence is None
                else max(gate.last_tool_sequence, raw_sequence)
            )
        # A newly observed call re-gates audio from this point onward.
        if gate.grace_task is not None:
            gate.grace_task.cancel()
            gate.grace_task = None
        gate.released = False
        key = self._tool_key(response_id, call_id)
        existing = socket.tool_tasks.get(key)
        if existing is not None and not existing.done():
            logger.info(
                "qwen function_call already running lesson=%s response_id=%s call_id=%s",
                lesson_id, response_id, call_id,
            )
            return
        replay = call_id in socket.tool_call_results or (state.submitted and state.settled)
        if replay:
            state.replay = True
        state.submitted = True
        task = asyncio.create_task(
            self._run_tool_task(
                socket, lesson_id, event, response_id, call_id, key, replay=replay
            ),
            name=f"qwen-tool-{lesson_id}-{response_id}-{call_id}",
        )
        socket.tool_tasks[key] = task

    async def _run_tool_task(
        self,
        socket: LessonSocket,
        lesson_id: str,
        event: dict[str, Any],
        response_id: str,
        call_id: str,
        key: str,
        *,
        replay: bool,
    ) -> None:
        ok = False
        try:
            await self._handle_qwen_tool_call(
                socket, lesson_id, event, response_id=response_id
            )
            cached = socket.tool_call_results.get(key)
            ok = bool(cached.get("ok")) if isinstance(cached, dict) else False
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - tool task must never crash the loop
            logger.exception("qwen tool task failed lesson=%s call_id=%s", lesson_id, call_id)
            ok = False
        finally:
            if socket.tool_tasks.get(key) is asyncio.current_task():
                socket.tool_tasks.pop(key, None)
        await self._on_tool_call_settled(
            socket, lesson_id, response_id, call_id, ok=ok, replay=replay
        )

    async def _on_tool_call_settled(
        self,
        socket: LessonSocket,
        lesson_id: str,
        response_id: str,
        call_id: str,
        *,
        ok: bool,
        replay: bool,
    ) -> None:
        gate = self._response_gate(socket, response_id, create=False)
        if gate is None:
            return
        state = gate.calls.setdefault(call_id, ToolCallState(call_id=call_id))
        state.settled = True
        state.ok = ok
        state.replay = state.replay or replay
        if not ok:
            # Never speak a confirmation for a tool that did not persist.
            await self._cancel_buffered_audio(
                socket, response_id=response_id, reason="tool_failed"
            )
            if gate.done_event is not None:
                done_event = gate.done_event
                gate.done_event = None
                await self._finalize_response(socket, done_event)
            return
        if gate.discarded:
            if gate.done_event is not None:
                done_event = gate.done_event
                gate.done_event = None
                await self._finalize_response(socket, done_event)
            return
        if gate.has_pending_calls():
            return
        await self._flush_audio_buffer(socket, gate, reason="tool_calls_complete")

    def _gate_has_flow_control_call(self, gate: ResponseGate | None) -> bool:
        if gate is None:
            return False
        return any(
            call.name in {"wait_for_student", "lesson_finish"}
            for call in gate.calls.values()
        )

    async def _finalize_response(
        self, socket: LessonSocket, event: dict[str, Any]
    ) -> None:
        """Handle a Qwen response.done once its audio gate and tools are settled."""
        lesson_id = socket.lesson_id
        response = event.get("response") if isinstance(event.get("response"), dict) else {}
        response_id = str(
            response.get("id") or event.get("response_id") or socket.active_response_id or ""
        )
        status = str(response.get("status") or "")
        gate = self._response_gate(socket, response_id, create=False)
        if gate is not None and gate.finalized:
            return
        if gate is not None:
            gate.finalized = True
        lifecycle = socket.response_lifecycles.get(response_id)
        if lifecycle is not None:
            lifecycle.completed_at = time.monotonic()
            lifecycle.status = status or lifecycle.status or "done"
        socket.active_response_id = None if socket.active_response_id == response_id else socket.active_response_id
        has_board_call_in_response = bool(gate.has_board_call) if gate is not None else False
        board_satisfied = self._startup_board_ready(socket)
        logger.info(
            "qwen response.done lesson=%s response_id=%s status=%s has_audio=%s has_text=%s has_function_call=%s board_tool_call=%s board_satisfied=%s",
            lesson_id, response_id, status,
            bool(lifecycle.has_audio) if lifecycle else None,
            bool(lifecycle.has_text) if lifecycle else None,
            bool(lifecycle.has_function_call) if lifecycle else None,
            has_board_call_in_response,
            board_satisfied,
        )
        if status in {"failed", "cancelled", "incomplete"}:
            logger.warning(
                "qwen response incomplete lesson=%s response_id=%s status=%s board_satisfied=%s",
                lesson_id, response_id, status, board_satisfied,
            )
        if status in {"failed", "cancelled"}:
            await self._cancel_buffered_audio(
                socket, response_id=response_id, reason=f"response_{status}"
            )

        if gate is not None and gate.gap_detected:
            # Source-side sequence gap: regenerate the teaching step rather than
            # letting a half-delivered explanation stall the lesson.
            await self._request_response(
                socket, reason="audio_sequence_gap_recovery", force=True
            )
            return

        now_mono = time.monotonic()
        director_busy = (
            socket.last_board_director_started_at > 0
            and socket.last_board_director_finished_at < socket.last_board_director_started_at
            and (now_mono - socket.last_board_director_started_at) < 120
        )
        can_nudge = (
            not socket.closed.is_set()
            and not board_satisfied
            and socket.qwen is not None
            and socket.active_response_id is None
            and not director_busy
            and socket.startup_nudge_count < 3
            and (now_mono - socket.startup_last_nudge_at) >= 3.0
        )
        recovery_nudged = False
        if can_nudge and not has_board_call_in_response:
            recovery_nudged = True
            socket.startup_nudge_count += 1
            socket.startup_last_nudge_at = now_mono
            socket.startup_phase = LessonStartupPhase.RESPONSE_STARTED if (socket.last_qwen_audio_at or socket.last_qwen_transcript_at) else LessonStartupPhase.PROMPT_SENT
            logger.warning(
                "response.done without board tool call during startup lesson=%s response_id=%s nudge=%s",
                lesson_id, response_id, socket.startup_nudge_count,
            )
            await self._send_startup_status(socket, "recovering", "Preparing your first lesson...")
            await self._prompt_text(
                socket,
                "System instruction: The concept diagram is still missing. If the lesson title is not on the board, "
                "write it first with board_write_on_board, then immediately call create_board_visual to draw a diagram "
                "explaining the core concept. Say the cue phrases aloud as the stages appear, then keep teaching. "
                "Do not wait for the student.",
                reason="startup_recovery_no_board_call",
            )
        elif can_nudge and has_board_call_in_response and not board_satisfied:
            recovery_nudged = True
            socket.startup_nudge_count += 1
            socket.startup_last_nudge_at = now_mono
            logger.warning(
                "response.done had board_intent but no board event lesson=%s response_id=%s nudge=%s director_error=%s",
                lesson_id, response_id, socket.startup_nudge_count, socket.last_board_director_error,
            )
            await self._send_startup_status(socket, "recovering", "Preparing your first lesson...")
            await self._prompt_text(
                socket,
                "System instruction: The previous diagram attempt did not produce a visible board update. "
                "Call create_board_visual again with a clear intent for a diagram that explains the core concept, "
                "using accurate short labels and stage cues. Then continue teaching aloud.",
                reason="startup_recovery_board_failed",
            )

        if recovery_nudged:
            return

        if lifecycle is not None and lifecycle.has_function_call:
            # Tool continuations are hub-managed and only after a successful,
            # persisted result. wait_for_student/lesson_finish stop here.
            if (
                lifecycle.function_call_executed_ok is True
                and not self._gate_has_flow_control_call(gate)
            ):
                await self._request_response(
                    socket, reason="tool_continuation", force=True
                )
            return

        await self._maybe_auto_continue(
            socket, lesson_id, response_id, status, lifecycle
        )
        await self._schedule_continuation_watchdog(
            socket, lesson_id, response_id, status, lifecycle
        )

    async def _schedule_continuation_watchdog(
        self,
        socket: LessonSocket,
        lesson_id: str,
        response_id: str,
        status: str,
        lifecycle: ResponseLifecycle | None,
    ) -> None:
        """Recover a teaching step that ended with no audio/text/tool call."""
        if lifecycle is not None and (
            lifecycle.has_audio or lifecycle.has_text or lifecycle.has_function_call
        ):
            return

        async def _watch() -> None:
            try:
                await asyncio.sleep(CONTINUATION_WATCHDOG_SECONDS)
                if socket.closed.is_set() or socket.lesson_finished:
                    logger.info("continuation watchdog skipped lesson=%s reason=closed_or_finished", lesson_id)
                    return
                if socket.waiting_for_student:
                    logger.info("continuation watchdog skipped lesson=%s reason=waiting_for_student", lesson_id)
                    return
                if socket.active_response_id is not None:
                    logger.info("continuation watchdog skipped lesson=%s reason=active_response=%s", lesson_id, socket.active_response_id)
                    return
                started = socket.response_lifecycles.get(response_id)
                if started is not None and (
                    started.has_audio or started.has_text or started.has_function_call
                ):
                    return
                logger.warning(
                    "continuation watchdog: empty teaching response lesson=%s response_id=%s status=%s",
                    lesson_id, response_id, status,
                )
                await self._request_response(socket, reason="continuation_watchdog_empty")
            except asyncio.CancelledError:
                return
            except Exception:  # pragma: no cover
                logger.warning("continuation watchdog failed lesson=%s", lesson_id, exc_info=True)

        asyncio.create_task(
            _watch(), name=f"continue-watchdog-{lesson_id}-{response_id}"
        )

    # -- helpers ----------------------------------------------------------

    async def _maybe_start_initial_teaching(
        self,
        socket: LessonSocket,
        lesson: Lesson,
        detail: dict[str, Any],
        board_envelope: dict[str, Any],
        transcript_cursor: Any,
    ) -> None:
        if socket.qwen is None or socket.closed.is_set():
            return
        status = str(getattr(lesson, "status", "") or "").lower()
        if status in {"completed", "ended", "cancelled", "canceled"}:
            logger.info("skip initial teaching: lesson ended lesson=%s status=%s", lesson.id, status)
            return

        last_seq = 0
        try:
            last_seq = int(board_envelope.get("last_sequence") or board_envelope.get("revision") or 0)
        except Exception:
            last_seq = 0
        cursor_val = 0
        try:
            if isinstance(transcript_cursor, dict):
                cursor_val = int(transcript_cursor.get("cursor") or transcript_cursor.get("sequence") or 0)
            else:
                cursor_val = int(transcript_cursor or 0)
        except Exception:
            cursor_val = 0

        # If board content already exists from this session/startup, don't restart.
        if lesson.id in self._startup_completed_lessons or self._startup_board_ready(socket):
            logger.info(
                "skip initial teaching: startup already satisfied lesson=%s lease=%s last_seq=%s",
                lesson.id, socket.lease_id, last_seq,
            )
            socket.startup_phase = LessonStartupPhase.READY
            return
        # Same socket already prompted and not failed - prevent duplicates.
        if socket.initial_teaching_started and socket.startup_phase not in {LessonStartupPhase.FAILED, LessonStartupPhase.IDLE}:
            logger.info("skip initial teaching: already started on socket lesson=%s lease=%s phase=%s", lesson.id, socket.lease_id, socket.startup_phase.value)
            return

        startup_lock = self._startup_locks.setdefault(lesson.id, asyncio.Lock())
        async with startup_lock:
            # Re-check under lock.
            if socket.closed.is_set() or socket.qwen is None:
                return
            async with self._lock:
                active_lease = self._startup_active_lease.get(lesson.id)
                current = self._sockets.get(lesson.id)
                if lesson.id in self._startup_completed_lessons or self._startup_board_ready(socket):
                    socket.startup_phase = LessonStartupPhase.READY
                    return
                if active_lease and active_lease != socket.lease_id and current is not None and not current.closed.is_set():
                    logger.info(
                        "skip initial teaching: another lease is starting lesson=%s active=%s current=%s",
                        lesson.id, active_lease, socket.lease_id,
                    )
                    return
                self._startup_active_lease[lesson.id] = socket.lease_id

            socket.startup_attempt += 1
            socket.startup_phase = LessonStartupPhase.PROMPT_SENT
            socket.initial_teaching_started = True
            socket.startup_has_board_tool_call = False
            socket.startup_has_board_event = socket.startup_has_board_event or socket.last_board_event_at > 0
            socket.startup_nudge_count = 0
            socket.startup_last_nudge_at = 0.0
            socket.last_board_director_error = None
            socket.last_board_director_started_at = 0.0
            socket.last_board_director_finished_at = 0.0

            is_fresh = last_seq <= 0 and cursor_val <= 0
            topic = str(detail.get("topic") or getattr(lesson, "topic", None) or "this lesson")
            subject = str(detail.get("subject") or getattr(lesson, "subject", None) or "General")
            level = str(detail.get("learning_level") or getattr(lesson, "learning_level", None) or "beginner")
            plan = detail.get("plan") or detail.get("lesson_plan") or {}

            if is_fresh:
                prompt = (
                    "System instruction: Start the live lesson now. "
                    f"Topic: {topic}. Subject: {subject}. Learning level: {level}. "
                    f"Lesson plan/context: {json.dumps(plan, ensure_ascii=False)[:2000]}. "
                    "Begin teaching immediately without waiting for the student to speak first. "
                    "Your first board call must be board_write_on_board writing ONLY the lesson title as ONE short line (style h2). "
                    "Your second board call must be create_board_visual: draw a clear diagram that explains the central concept of this topic, "
                    "with accurate short labels. The tool result lists 'cue_phrases'; say each exact phrase aloud while explaining its stage so the diagram reveals with your speech. "
                    "Do not write definition, intuition or example lines before that diagram exists. "
                    "After the diagram, write ONE short line per board_write_on_board call "
                    "(1-8 words, one or two words is fine, no newlines, no paragraphs, no lists), each line matching what you are "
                    "explaining at that moment; make a separate call for every additional line. Never dump a block of text or "
                    "several lines in a single call, and never repeat a line already on the board. "
                    "Write actual teaching content, not meta labels or narration: never write standalone labels like "
                    "'Definition', 'Intuition', 'Example', or 'Step 1'; write the real definition/intuition/example content. "
                    "never supply x/y coordinates - describe the semantic position and the backend layout manager places it. "
                    "Keep teaching automatically through the lesson plan after each spoken turn - the backend "
                    "starts your next response for you, so never stop just because you asked a rhetorical question or a quick check. "
                    "Call wait_for_student ONLY when you need the student's actual answer before you can continue; that call unlocks their microphone automatically, then listen."
                )
            else:
                prompt = (
                    "System instruction: Resume this live lesson without restarting from the beginning. "
                    f"Topic: {topic}. Subject: {subject}. Learning level: {level}. "
                    f"Current lesson state: {json.dumps(detail.get('lesson_state') or {}, ensure_ascii=False)[:1500]}. "
                    f"Board last_sequence={last_seq}, transcript_cursor={cursor_val}. "
                    "Briefly reorient the student and continue from the current progress. "
                    "If the board has only the title and no concept diagram, call create_board_visual next before writing more explanation; "
                    "say each returned cue phrase aloud as its stage is explained. "
                    "Do not repeat already completed content or any line already on the board; use board_intent only for new or resumed visuals. "
                    "Write each new heading, definition or equation with board_write_on_board as ONE short line per call "
                    "(1-8 words, no newlines, no paragraphs, no lists), aligned with what you say aloud; make a separate call per line. "
                    "Write actual teaching content, not meta labels or narration: never write standalone labels like "
                    "'Definition', 'Intuition', 'Example', or 'Step 1'; write the real content. "
                    "Use create_board_visual only for new structured visuals with one-line labels of actual content; "
                    "leave physical placement to the backend layout manager. Keep teaching automatically after each spoken turn; "
                    "call wait_for_student only when you truly need the student's answer, which unlocks their microphone automatically; otherwise continue explaining."
                )

            logger.info(
                "initial teaching prompt lesson=%s fresh=%s attempt=%s lease=%s topic=%s",
                lesson.id, is_fresh, socket.startup_attempt, socket.lease_id, topic,
            )
            await self._send_startup_status(socket, "prompt_sent", "Preparing your first lesson...")
            if socket.active_response_id is not None:
                logger.warning("initial teaching prompt deferred: active response lesson=%s response=%s", lesson.id, socket.active_response_id)
                # Watchdog/response.done will handle recovery; do not create duplicate response.
                return
            try:
                sent = await self._prompt_text(
                    socket, prompt, reason="initial_teaching"
                )
                if not sent:
                    raise RuntimeError("initial teaching prompt was not submitted")
                logger.info("initial teaching instruction submitted lesson=%s fresh=%s attempt=%s", lesson.id, is_fresh, socket.startup_attempt)
            except Exception:
                socket.startup_phase = LessonStartupPhase.FAILED
                async with self._lock:
                    if self._startup_active_lease.get(lesson.id) == socket.lease_id:
                        self._startup_active_lease.pop(lesson.id, None)
                logger.exception("initial teaching prompt failed lesson=%s", lesson.id)
                raise

    async def _send_startup_status(self, socket: LessonSocket, phase: str, message: str) -> None:
        if socket.closed.is_set():
            return
        payload = {
            MSG_TYPE: "startup.status",
            "phase": phase,
            "message": message,
        }
        try:
            await socket.websocket.send_json(payload)
        except Exception:  # pragma: no cover
            logger.debug("startup status send failed lesson=%s phase=%s", socket.lesson_id, phase)

    def _director_busy(self, socket: LessonSocket, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        started = socket.last_board_director_started_at
        if started <= 0:
            return False
        finished = socket.last_board_director_finished_at
        if finished >= started:
            return False
        return (now - started) < 120

    def _startup_status_message(self, socket: LessonSocket) -> str:
        if self._startup_board_ready(socket):
            return "Restoring your classroom..."
        if self._director_busy(socket):
            return "Creating the first board illustration..."
        if socket.last_board_director_error and not (socket.last_qwen_audio_at or socket.last_qwen_transcript_at):
            return "Preparing your first lesson..."
        if socket.last_qwen_audio_at > 0 or socket.last_qwen_transcript_at > 0:
            # Tutor is speaking/responding but board not ready yet.
            return "Creating the first board illustration..."
        if socket.last_qwen_response_created_at > 0:
            return "Waiting for tutor response..."
        return "Connecting to the tutor..."

    def _startup_failure_message(self, socket: LessonSocket) -> tuple[str, str, bool]:
        """Return (code, message, fatal_error) for a startup failure.

        fatal_error=False means show inline status only; do not raise a scary centered error.
        """
        if socket.last_board_director_error:
            return "board_start_failed", f"The board illustration could not be created: {socket.last_board_director_error}", True
        if socket.last_tool_call_at > 0 and not self._startup_board_ready(socket):
            return "board_start_failed", "The first concept diagram was not created. Please retry.", True
        if socket.last_qwen_audio_at > 0 or socket.last_qwen_transcript_at > 0:
            return "board_start_delayed", "The tutor is speaking, but the first board visual is still being prepared.", False
        if socket.last_qwen_response_created_at > 0:
            return "tutor_start_delayed", "The tutor started responding, but the first board visual is still being prepared.", False
        return "tutor_start_delayed", "The tutor connection was established, but the lesson did not start. Please retry.", True

    async def _maybe_auto_continue(
        self,
        socket: LessonSocket,
        lesson_id: str,
        response_id: str,
        status: str,
        lifecycle: ResponseLifecycle | None,
    ) -> None:
        """Start the next teaching turn unless the tutor asked to wait.

        The model is told (in session instructions) to keep teaching after each
        response. DashScope does not create that next response by itself, so we
        drive it with a bare ``response.create`` — no synthetic user message is
        added to the conversation or transcript.
        """
        def _skip(reason: str) -> None:
            logger.info(
                "auto-continue skipped lesson=%s response_id=%s reason=%s",
                lesson_id, response_id, reason,
            )

        if socket.closed.is_set() or socket.qwen is None:
            _skip("socket_closed")
            return
        if status in {"failed", "cancelled"}:
            _skip(f"response_{status}")
            return
        if socket.waiting_for_student or socket.lesson_finished:
            _skip("waiting_for_student" if socket.waiting_for_student else "lesson_finished")
            return
        if socket.active_response_id is not None:
            _skip(f"active_response={socket.active_response_id}")
            return
        if not (socket.startup_has_board_event or socket.last_board_event_at > 0):
            _skip("startup_board_missing")  # startup recovery owns the conversation
            return
        # A successful tool call already requests its own continuation after the
        # result has been persisted and acknowledged.
        if lifecycle is not None and lifecycle.has_function_call:
            _skip("function_call_owns_continuation")
            return
        # Do not spin on empty/completion-only responses; the watchdog covers them.
        if lifecycle is None or not (lifecycle.has_audio or lifecycle.has_text):
            _skip("empty_response")
            return
        now = time.monotonic()
        if (now - socket.last_auto_continue_at) < AUTO_CONTINUE_COOLDOWN_SECONDS:
            _skip("cooldown")
            return
        if socket.auto_continue_count >= MAX_AUTO_CONTINUES_PER_TURN:
            logger.warning(
                "auto-continue cap reached lesson=%s response_id=%s count=%s (student turn will reset)",
                lesson_id, response_id, socket.auto_continue_count,
            )
            return
        socket.auto_continue_count += 1
        socket.last_auto_continue_at = now
        # This path has its own 1s cooldown above; the response has already
        # completed, so the creation-time cooldown must not stall the step.
        requested = await self._request_response(
            socket, reason="auto_continue", force=True
        )
        if requested:
            logger.info(
                "auto-continue teaching lesson=%s response_id=%s count=%s",
                lesson_id, response_id, socket.auto_continue_count,
            )

    async def _safe_prompt_text(self, socket: LessonSocket, text: str, *, reason: str) -> bool:
        if socket.closed.is_set() or socket.qwen is None:
            return False
        if socket.active_response_id is not None:
            logger.info("skip prompt_text reason=%s lesson=%s active_response=%s", reason, socket.lesson_id, socket.active_response_id)
            return False
        now = time.monotonic()
        if (now - socket.startup_last_nudge_at) < 2.0:
            logger.info("skip prompt_text cooldown reason=%s lesson=%s", reason, socket.lesson_id)
            return False
        socket.startup_last_nudge_at = now
        sent = await self._prompt_text(socket, text, reason=reason)
        if sent:
            logger.info("prompt_text sent reason=%s lesson=%s lease=%s", reason, socket.lesson_id, socket.lease_id)
        return sent

    def _schedule_startup_watchdog(self, socket: LessonSocket, lesson_id: str) -> None:
        async def _watch() -> None:
            try:
                checkpoints = [6.0, 12.0, 20.0, 30.0]
                prev = 0.0
                for checkpoint in checkpoints:
                    await asyncio.sleep(max(0.0, checkpoint - prev))
                    prev = checkpoint
                    if socket.closed.is_set():
                        return
                    if self._startup_board_ready(socket):
                        socket.startup_phase = LessonStartupPhase.READY
                        return

                    now = time.monotonic()
                    if self._director_busy(socket, now):
                        await self._send_startup_status(socket, "board_director", "Creating the first board illustration...")
                        continue

                    status_msg = self._startup_status_message(socket)
                    has_response_activity = bool(
                        socket.last_qwen_response_created_at
                        or socket.last_qwen_audio_at
                        or socket.last_qwen_transcript_at
                    )
                    logger.warning(
                        "startup watchdog lesson=%s lease=%s attempt=%s nudges=%s phase=%s status=%r resp_created=%s audio=%s transcript=%s tool=%s director_started=%s director_finished=%s director_error=%s board=%s active_resp=%s",
                        lesson_id, socket.lease_id, socket.startup_attempt, socket.startup_nudge_count, socket.startup_phase.value,
                        status_msg,
                        bool(socket.last_qwen_response_created_at), bool(socket.last_qwen_audio_at),
                        bool(socket.last_qwen_transcript_at), bool(socket.last_tool_call_at),
                        bool(socket.last_board_director_started_at), bool(socket.last_board_director_finished_at),
                        socket.last_board_director_error,
                        bool(socket.last_board_event_at),
                        socket.active_response_id,
                    )

                    await self._send_startup_status(socket, "waiting", status_msg)

                    # Bounded recovery nudges. Do not nudge while director/response is active.
                    if socket.startup_nudge_count < 3 and socket.active_response_id is None and not self._director_busy(socket, now):
                        if not has_response_activity:
                            nudge = (
                                "System instruction: The classroom is connected. Begin teaching now. "
                                "First call board_write_on_board to write ONLY the lesson title as one short line (style h2). "
                                "Immediately call create_board_visual to draw a concept diagram and say its cue phrases aloud as stages appear. "
                                "Then write one short line per call (1-8 words, no newlines) as you teach. Keep teaching automatically; "
                                "only call wait_for_student when you need the student's answer."
                            )
                            reason = "watchdog_nudge_start"
                        else:
                            nudge = (
                                "System instruction: Continue teaching, but the concept diagram is still missing. "
                                "Call create_board_visual now with accurate short labels and stage cues, then continue explaining aloud. "
                                "Write one short line per call (1-8 words, no newlines) only after the diagram exists. "
                                "Keep teaching after each spoken turn; pause only via wait_for_student."
                            )
                            reason = "watchdog_nudge_board_intent"
                        sent = await self._safe_prompt_text(socket, nudge, reason=reason)
                        if sent:
                            socket.startup_nudge_count += 1
                            await self._send_startup_status(socket, reason, "Preparing your first lesson...")

                # Final check: if still no concept diagram, report a clear failure but keep the session alive for retry.
                if not socket.closed.is_set() and not self._startup_board_ready(socket):
                    if self._director_busy(socket):
                        await self._send_startup_status(socket, "board_director", "Creating the first board illustration...")
                        logger.info("startup watchdog final check deferred: director busy lesson=%s", lesson_id)
                        return
                    code, message, fatal_error = self._startup_failure_message(socket)
                    socket.startup_phase = LessonStartupPhase.FAILED if fatal_error else socket.startup_phase
                    async with self._lock:
                        if self._startup_active_lease.get(lesson_id) == socket.lease_id:
                            self._startup_active_lease.pop(lesson_id, None)
                    logger.error(
                        "startup incomplete: no board event lesson=%s lease=%s attempt=%s nudges=%s code=%s fatal=%s msg=%s resp=%s audio=%s transcript=%s tool=%s director_err=%s",
                        lesson_id, socket.lease_id, socket.startup_attempt, socket.startup_nudge_count, code, fatal_error, message,
                        bool(socket.last_qwen_response_created_at), bool(socket.last_qwen_audio_at),
                        bool(socket.last_qwen_transcript_at), bool(socket.last_tool_call_at),
                        socket.last_board_director_error,
                    )
                    await self._send_startup_status(socket, "failed" if fatal_error else "delayed", message)
                    if fatal_error:
                        try:
                            await socket.websocket.send_json(
                                {
                                    MSG_TYPE: MSG_ERROR,
                                    "code": code,
                                    "message": message,
                                    "retryable": True,
                                    "phase": "startup",
                                }
                            )
                        except Exception:  # pragma: no cover
                            pass
            except asyncio.CancelledError:
                return
            except Exception:  # pragma: no cover
                logger.exception("startup watchdog failed lesson=%s", lesson_id)

        asyncio.create_task(_watch())

    async def _active_socket(self, lesson_id: str) -> LessonSocket | None:
        async with self._lock:
            return self._sockets.get(lesson_id)

    async def _end_lesson(
        self,
        socket: LessonSocket,
        lesson_id: str,
        reason: str,
    ) -> None:
        """End the lesson (graph finalize + board snapshot) and notify client."""
        await self._lock_answer_mic(socket, reason="lesson_ended")
        await self._cancel_pending_work(socket, reason="lesson_ended")
        try:
            async with session_scope() as fresh_db:
                lesson = await fresh_db.get(Lesson, lesson_id)
                if lesson is None:
                    return
                lesson = await graph_service.finish_lesson(fresh_db, lesson_id, reason)
                detail = _detail_dict(await lessons_service.build_lesson_detail(fresh_db, lesson))
                board_envelope = await get_board_snapshot(
                    fresh_db, lesson, include_events=False
                )
            async with self._lock:
                self._startup_completed_lessons.discard(lesson_id)
                self._startup_active_lease.pop(lesson_id, None)
            await socket.websocket.send_json(
                {
                    MSG_TYPE: MSG_SESSION_ENDED,
                    "reason": reason,
                    "needs_notes_prompt": True,
                    "lesson": detail,
                    "board": _jsonable(board_envelope),
                }
            )
        except Exception as exc:  # pragma: no cover
            logger.exception("end lesson failed lesson=%s", lesson_id)
            await _send_error(socket, "internal_error", "could not end lesson")

    def _transcript_entry_from_qwen(
        self, lesson_id: str, event: dict[str, Any]
    ) -> dict[str, Any] | None:
        text = str(event.get("transcript") or event.get("text") or "").strip()
        if not text:
            return None
        speaker = "tutor" if event.get("speaker") != "student" else "student"
        now = datetime.now(timezone.utc)
        return {
            "id": uuid.uuid4().hex,
            "session_id": lesson_id,
            "sequence": 0,  # filled by persistence
            "speaker": speaker,
            "text": text,
            "started_at": now.isoformat(),
            "ended_at": now.isoformat(),
        }

    async def _persist_transcript(self, lesson_id: str, entry: dict[str, Any]) -> None:
        try:
            started_at = entry.get("started_at")
            ended_at = entry.get("ended_at")
            from datetime import datetime

            started = datetime.fromisoformat(started_at) if started_at else None
            ended = datetime.fromisoformat(ended_at) if ended_at else None
            async with session_scope() as db:
                await append_transcript_entry(
                    db,
                    lesson_id,
                    entry["speaker"],
                    entry["text"],
                    started_at=started,
                    ended_at=ended,
                )
        except Exception:  # pragma: no cover - transcript must not block audio
            logger.warning("transcript persist failed lesson=%s", lesson_id, exc_info=True)


def _MSG_TYPE() -> str:
    return "type"


MSG_TYPE = _MSG_TYPE()


async def _send_close(
    socket: LessonSocket, http_status: int, code: int, message: str
) -> None:
    try:
        await socket.websocket.send_json(
            {MSG_TYPE: MSG_ERROR, "code": _http_code_to_error(http_status), "message": message}
        )
    except Exception:  # pragma: no cover
        pass
    try:
        await socket.websocket.close(code=code)
    except Exception:  # pragma: no cover
        pass


async def _send_error(
    socket: LessonSocket, code: str, message: str, *, retryable: bool = False
) -> None:
    try:
        await socket.websocket.send_json(
            {MSG_TYPE: MSG_ERROR, "code": code, "message": message, "retryable": retryable}
        )
    except Exception:  # pragma: no cover
        pass


def _http_code_to_error(status: int) -> str:
    mapping = {
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        409: "conflict",
        422: "invalid_arguments",
        429: "rate_limited",
        503: "qwen_unavailable",
    }
    return mapping.get(status, "internal_error")


def _jsonable(value: Any) -> Any:
    """Convert DTOs/dataclasses to JSON-safe structures for ``send_json``."""
    from fastapi.encoders import jsonable_encoder

    return jsonable_encoder(value)


def _detail_dict(detail: Any) -> dict[str, Any]:
    """Normalise a lesson-detail DTO (Pydantic model or mapping) to a plain dict."""
    if detail is None:
        return {}
    if isinstance(detail, Mapping):
        return dict(detail)
    dump = getattr(detail, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    return {}


async def _lesson_detail(lesson_id: str) -> dict[str, Any]:
    async with session_scope() as db:
        lesson = await db.get(Lesson, lesson_id)
        if lesson is None:
            return {}
        return _detail_dict(await lessons_service.build_lesson_detail(db, lesson))


def _teacher_instructions(detail: Mapping[str, Any] | Any) -> str:
    """Build the Qwen teacher instructions from the lesson plan/state."""
    detail = _detail_dict(detail)
    topic = str(detail.get("topic") or "the topic")
    subject = str(detail.get("subject") or "General Studies")
    level = str(detail.get("learning_level") or "beginner")
    plan = detail.get("plan")
    concepts = plan.get("concepts", []) if isinstance(plan, Mapping) else []
    concept_list = ", ".join(str(c.get("title", "")) for c in concepts) if concepts else "the lesson concepts"
    return (
        f"You are a patient, encouraging private tutor teaching '{topic}' ({subject}) "
        f"at {level} level. You speak out loud while writing on a shared blackboard. "
        f"Concepts to cover: {concept_list}. "
        f"Rules: (1) Write the lesson title FIRST with board_write_on_board using "
        f"style h2, as ONE short line. (2) IMMEDIATELY after the title, call "
        f"create_board_visual to draw a diagram that explains the central concept, before "
        f"any definition or explanation lines. The tool result lists 'cue_phrases'; say "
        f"each exact phrase aloud while explaining that stage so the diagram reveals with "
        f"your speech. (3) Then teach aloud and write on the board ONE SHORT LINE PER TOOL "
        f"CALL: each board_write_on_board call must contain only one line of 1-8 words "
        f"(one or two words is fine), with no newlines, no paragraphs and no lists. Make a "
        f"separate call for every new line, and let each line say only what you are "
        f"explaining at that moment so the board grows naturally with your speech. NEVER "
        f"dump a long text or several lines in a single call, and never repeat a line "
        f"already on the board. (4) Write actual teaching content, not meta labels or "
        f"narration. NEVER write standalone labels/placeholders like 'Definition', "
        f"'Intuition', 'Example', 'Step 1', 'Explanation', or 'Let's start'; instead write "
        f"the real definition, intuition, example, or step content itself. Do not write "
        f"what you are about to do; write the content. (5) KEEP TEACHING AUTOMATICALLY. "
        f"The backend starts your next response as soon as yours ends, so continue with "
        f"the next small teaching step instead of stopping or staying silent. Each new "
        f"board line is placed below the previous one; when the board fills it is cleared "
        f"automatically and you should simply keep teaching. Do not wait for the student "
        f"after every question: rhetorical questions and quick understanding checks are "
        f"part of your teaching, so carry on explaining them yourself. (6) Call "
        f"wait_for_student ONLY when you have asked a direct question and genuinely need "
        f"the student's answer before you can continue. That call unlocks the student's "
        f"microphone automatically; stop speaking and listen for the answer. Never call "
        f"it for rhetorical questions. (7) Keep every diagram label to one short line of "
        f"accurate content next to the feature it names; never invent decorative parts. "
        f"(8) Never modify database state directly — only through the exposed tools. "
        f"(9) Keep explanations concise and use lesson_finish when the student is done "
        f"or time is up."
    )


def _openai_tools() -> list[dict[str, Any]]:
    from app.board.registry import ToolRegistry

    # Realtime model receives intent + lesson tools only; low-level board tools are internal.
    return ToolRegistry().public_openai_tools()


__all__ = [
    "LessonRealtimeHub",
    "LessonSocket",
    "QwenSession",
    "CLOSE_SUPERSEDED",
    "CLOSE_FORBIDDEN",
    "CLOSE_LESSON_ENDED",
    "CLOSE_QWEN_UNAVAILABLE",
]
