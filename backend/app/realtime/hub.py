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
MAX_AUTO_CONTINUES_PER_TURN = 60
AUTO_CONTINUE_COOLDOWN_SECONDS = 1.0

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
        self, call_id: str, output: str, *, continue_response: bool = True
    ) -> None:
        ...

    async def prompt_text(self, text: str) -> None:
        """Send a text instruction into the conversation and request a response."""
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
    # Visible-board recycling: the client reports measured fullness; the server
    # owns the persisted wipe so snapshots/replay/Qwen context stay in sync.
    last_viewport_report_sequence: int = -1
    last_viewport_cleared_sequence: int = -1
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
            if socket.last_board_director_finished_at == 0.0 and socket.last_board_director_started_at > 0:
                socket.last_board_director_finished_at = time.monotonic()
            async with self._lock:
                self._startup_completed_lessons.add(lesson_id)
                if self._startup_active_lease.get(lesson_id) == socket.lease_id:
                    self._startup_active_lease.pop(lesson_id, None)
            event_type = event.get("type") if isinstance(event, dict) else None
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
                cursor = await get_transcript_cursor(db, lesson.id)

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
                if socket.lesson_id not in self._startup_completed_lessons and not socket.startup_has_board_event:
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

    async def _handle_viewport_full(
        self,
        socket: LessonSocket,
        lesson_id: str,
        envelope: dict[str, Any],
    ) -> None:
        """Recycle the visible board once: validate, then persist a system clear."""
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

        async with socket.viewport_clear_lock:
            if sequence <= socket.last_viewport_cleared_sequence:
                return
            if sequence <= socket.last_viewport_report_sequence:
                return
            socket.last_viewport_report_sequence = sequence
            try:
                async with session_scope() as db:
                    lesson = await db.get(Lesson, lesson_id)
                    current_sequence = int(
                        getattr(lesson, "last_board_sequence", 0) or 0
                    )
                if sequence < current_sequence:
                    # The client is looking at an older board; let it catch up.
                    return
                from app.board.registry import ToolRegistry

                board = self._board or BoardService()
                # The committed clear must be broadcast to the live socket even
                # when this hub has no shared board service wired (tests/tests
                # harnesses), so make the publisher explicit here.
                board.set_publisher(self)
                registry = ToolRegistry(
                    board=board,
                    trigger_system=self._trigger_system,
                )
                registry.set_lesson_publisher(self)
                async with session_scope() as db:
                    result = await registry.execute_structured(
                        db,
                        lesson_id,
                        "board_clear_region",
                        {"scope": "all"},
                        source=BoardEventSource.SYSTEM,
                    )
            except Exception:  # pragma: no cover - never break the socket
                logger.exception("viewport board clear failed lesson=%s", lesson_id)
                await _send_error(socket, "internal_error", "could not recycle the board")
                return
            if not result.get("ok"):
                logger.warning("viewport board clear rejected lesson=%s result=%s", lesson_id, result)
                return
            socket.last_viewport_cleared_sequence = sequence
            logger.info(
                "visible board full -> cleared lesson=%s sequence=%s coverage=%.2f",
                lesson_id,
                sequence,
                coverage,
            )
            if (
                socket.qwen is not None
                and not socket.waiting_for_student
                and not socket.lesson_finished
            ):
                try:
                    await socket.qwen.prompt_text(
                        "System instruction: The student's visible board was full and "
                        "has been wiped. The board is empty now; continue teaching "
                        "from the cleared board with one short line per board_write_on_board call."
                    )
                except Exception:  # pragma: no cover
                    logger.warning("post-clear nudge failed lesson=%s", lesson_id, exc_info=True)

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
        try:
            if socket.qwen is not None and not socket.lesson_finished and not socket.waiting_for_student:
                await socket.qwen.create_response()
        except Exception:  # pragma: no cover
            logger.warning("qwen create_response on end failed lesson=%s", lesson_id, exc_info=True)

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
                    await socket.qwen.create_response()
                    logger.info("answer audio.stop -> qwen response requested lesson=%s", lesson_id)
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
            try:
                await socket.websocket.send_json(
                    {MSG_TYPE: MSG_AUDIO_TRUNCATED, "response_id": None, "played_ms": 0}
                )
            except Exception:  # pragma: no cover
                pass
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
        """Bridge Qwen server events -> client + tool call execution."""
        assert socket.qwen is not None
        audio_delta_count = 0
        response_has_board_tool_call: dict[str, bool] = {}
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
                    response_has_board_tool_call.setdefault(response_id, False)
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
                await socket.websocket.send_json(
                    {
                        MSG_TYPE: MSG_AUDIO_DELTA,
                        "data": event.get("delta", ""),
                        "sample_rate": get_settings().qwen_output_sample_rate,
                        "sequence": event.get("sequence"),
                    }
                )
                continue

            if etype in {"response.audio.done", "response.output_audio.done"}:
                response_id = str(event.get("response_id") or socket.active_response_id or "")
                logger.info("qwen audio.done lesson=%s response_id=%s type=%s", lesson_id, response_id, etype)
                await socket.websocket.send_json({MSG_TYPE: MSG_AUDIO_DONE, "response_id": event.get("response_id")})
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
                continue

            if etype == "response.function_call_arguments.done":
                response_id = str(event.get("response_id") or socket.active_response_id or "")
                if response_id and response_id in socket.response_lifecycles:
                    socket.response_lifecycles[response_id].has_function_call = True
                response_has_board_tool_call[response_id] = response_has_board_tool_call.get(response_id, False) or str(event.get("name") or "") == "board_intent"
                await self._handle_qwen_tool_call(socket, lesson_id, event, response_id=response_id)
                continue

            if etype == "response.done":
                response = event.get("response") if isinstance(event.get("response"), dict) else {}
                response_id = str(response.get("id") or event.get("response_id") or socket.active_response_id or "")
                status = str(response.get("status") or "")
                lifecycle = socket.response_lifecycles.get(response_id)
                if lifecycle is not None:
                    lifecycle.completed_at = time.monotonic()
                    lifecycle.status = status or lifecycle.status or "done"
                socket.active_response_id = None if socket.active_response_id == response_id else socket.active_response_id
                has_board_call_in_response = response_has_board_tool_call.get(response_id, False)
                board_satisfied = socket.startup_has_board_event or socket.last_board_event_at > 0
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

                # Controlled recovery during startup: if no board content yet, nudge for board_intent.
                # Do not nudge when a board_intent/director is already in progress or another response is active.
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
                    try:
                        await socket.qwen.prompt_text(
                            "System instruction: You responded, but no board visual was created. "
                            "Call board_intent now to draw the lesson heading and the first concept visual, then continue explaining aloud. "
                            "Do not wait for the student."
                        )
                    except Exception:  # pragma: no cover
                        logger.warning("startup recovery nudge failed lesson=%s", lesson_id, exc_info=True)
                elif can_nudge and has_board_call_in_response and not board_satisfied:
                    # board_intent was attempted in this response but did not result in a board event.
                    recovery_nudged = True
                    socket.startup_nudge_count += 1
                    socket.startup_last_nudge_at = now_mono
                    logger.warning(
                        "response.done had board_intent but no board event lesson=%s response_id=%s nudge=%s director_error=%s",
                        lesson_id, response_id, socket.startup_nudge_count, socket.last_board_director_error,
                    )
                    await self._send_startup_status(socket, "recovering", "Preparing your first lesson...")
                    try:
                        await socket.qwen.prompt_text(
                            "System instruction: The previous board drawing attempt did not produce a visible board update. "
                            "Call board_intent again with a clear intent for the lesson heading, a short introduction, "
                            "the first concept heading, one meaningful explanation, and a simple relevant diagram. "
                            "Then continue teaching aloud."
                        )
                    except Exception:  # pragma: no cover
                        logger.warning("startup recovery nudge after failed board_intent failed lesson=%s", lesson_id, exc_info=True)

                if not recovery_nudged:
                    await self._maybe_auto_continue(
                        socket, lesson_id, response_id, status, lifecycle
                    )
                continue

            if etype == "teacher.state":
                await socket.websocket.send_json(
                    {MSG_TYPE: MSG_TEACHER_STATE, **event.get("payload", {})}
                )
                continue

            # Unknown Qwen events are ignored (forwards compatibility).

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
                        await socket.qwen.function_call_output(
                            call_id,
                            json.dumps(cached),
                            continue_response=str(event.get("name") or "") != "wait_for_student",
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
                socket.response_lifecycles[response_id].function_call_executed_ok = ok
            if name == "board_intent":
                socket.last_board_director_finished_at = _time.monotonic()
                if not ok:
                    socket.last_board_director_error = str((result or {}).get("message") or (result or {}).get("error") or "board_intent_failed")
                else:
                    socket.last_board_director_error = None
                    socket.startup_has_board_event = True
                    socket.startup_phase = LessonStartupPhase.BOARD_EVENT_PERSISTED
                    if socket.last_board_event_at == 0:
                        socket.last_board_event_at = _time.monotonic()
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
        if lesson.id in self._startup_completed_lessons or socket.startup_has_board_event:
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
                if lesson.id in self._startup_completed_lessons:
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
                    "Then give a short spoken introduction. After that, write ONE short line per board_write_on_board call "
                    "(1-8 words, one or two words is fine, no newlines, no paragraphs, no lists), each line matching what you are "
                    "explaining at that moment; make a separate call for every additional line. Never dump a block of text or "
                    "several lines in a single call, and never repeat a line already on the board. "
                    "never supply x/y coordinates - describe the semantic position and the backend layout manager places it. "
                    "Use create_board_visual only when the concept genuinely needs a diagram, table or graph, and keep any labels "
                    "to one short line. Keep teaching automatically through the lesson plan after each spoken turn - the backend "
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
                    "Do not repeat already completed content or any line already on the board; use board_intent only for new or resumed visuals. "
                    "Write each new heading, definition or equation with board_write_on_board as ONE short line per call "
                    "(1-8 words, no newlines, no paragraphs, no lists), aligned with what you say aloud; make a separate call per line. "
                    "Use create_board_visual only for new structured visuals with one-line labels; "
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
                await socket.qwen.prompt_text(prompt)
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
        if socket.last_board_event_at > 0 or socket.startup_has_board_event:
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
        if socket.last_tool_call_at > 0 and socket.last_board_event_at == 0:
            return "board_start_failed", "A board drawing request was received, but no board update was shown. Please retry.", True
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
        if socket.closed.is_set() or socket.qwen is None:
            return
        if status in {"failed", "cancelled", "incomplete"}:
            return
        if socket.waiting_for_student or socket.lesson_finished:
            return
        if socket.active_response_id is not None:
            return
        if not (socket.startup_has_board_event or socket.last_board_event_at > 0):
            return  # startup recovery owns the conversation until the board exists
        if self._director_busy(socket):
            return
        # A tool call already triggers response.create via function_call_output;
        # creating another here would produce two overlapping responses.
        if lifecycle is not None and lifecycle.has_function_call:
            return
        # Do not spin on empty/completion-only responses.
        if lifecycle is None or not (lifecycle.has_audio or lifecycle.has_text):
            return
        now = time.monotonic()
        if (now - socket.last_auto_continue_at) < AUTO_CONTINUE_COOLDOWN_SECONDS:
            return
        if socket.auto_continue_count >= MAX_AUTO_CONTINUES_PER_TURN:
            logger.warning(
                "auto-continue cap reached lesson=%s response_id=%s count=%s (student turn will reset)",
                lesson_id, response_id, socket.auto_continue_count,
            )
            return
        socket.auto_continue_count += 1
        socket.last_auto_continue_at = now
        try:
            await socket.qwen.create_response()
            logger.info(
                "auto-continue teaching lesson=%s response_id=%s count=%s",
                lesson_id, response_id, socket.auto_continue_count,
            )
        except Exception:  # pragma: no cover
            logger.warning("auto-continue failed lesson=%s", lesson_id, exc_info=True)

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
        try:
            await socket.qwen.prompt_text(text)
            logger.info("prompt_text sent reason=%s lesson=%s lease=%s", reason, socket.lesson_id, socket.lease_id)
            return True
        except Exception:  # pragma: no cover
            logger.warning("prompt_text failed reason=%s lesson=%s", reason, socket.lesson_id, exc_info=True)
            return False

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
                    if socket.last_board_event_at > 0 or socket.startup_has_board_event:
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
                                "Start by calling board_write_on_board to write ONLY the lesson title as one short line (style h2), then explain it aloud. "
                                "Write one short line per call (1-8 words, no newlines) as you teach. Keep teaching automatically; "
                                "only call wait_for_student when you need the student's answer."
                            )
                            reason = "watchdog_nudge_start"
                        else:
                            nudge = (
                                "System instruction: Continue teaching, but you must create the first visible board content now. "
                                "Call board_write_on_board with the lesson title as one short line (style h2), then write one short line "
                                "per call (1-8 words, no newlines) as you teach. Use create_board_visual only if a diagram is genuinely needed. "
                                "Then continue explaining aloud. Keep teaching after each spoken turn; pause only via wait_for_student."
                            )
                            reason = "watchdog_nudge_board_intent"
                        sent = await self._safe_prompt_text(socket, nudge, reason=reason)
                        if sent:
                            socket.startup_nudge_count += 1
                            await self._send_startup_status(socket, reason, "Preparing your first lesson...")

                # Final check: if still no board event, report clear failure but keep session alive for retry.
                if not socket.closed.is_set() and socket.last_board_event_at == 0 and not socket.startup_has_board_event:
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
        f"style h2, as ONE short line. (2) Then teach aloud and write on the board "
        f"ONE SHORT LINE PER TOOL CALL: each board_write_on_board call must contain "
        f"only one line of 1-8 words (one or two words is fine), with no newlines, no "
        f"paragraphs and no lists. Make a separate call for every new line, and let "
        f"each line say only what you are explaining at that moment so the board "
        f"grows naturally with your speech. NEVER dump a long text or several lines "
        f"in a single call, and never repeat a line already on the board. "
        f"(3) KEEP TEACHING AUTOMATICALLY. The backend starts your next response as "
        f"soon as yours ends, so continue with the next small teaching step instead of "
        f"stopping or staying silent. Do not wait for the student after every question: "
        f"rhetorical questions and quick understanding checks are part of your teaching, "
        f"so carry on explaining them yourself. (4) Call wait_for_student ONLY when you "
        f"have asked a direct question and genuinely need the student's answer before "
        f"you can continue. That call unlocks the student's microphone automatically; "
        f"stop speaking and listen for the answer. Never call it for rhetorical "
        f"questions. (5) Use create_board_visual "
        f"only when a diagram, table or graph genuinely helps, and keep its text labels "
        f"to single short lines. (6) Never modify database state directly — only through "
        f"the exposed tools. (7) Keep explanations concise. (8) Use lesson_finish when "
        f"the student is done or time is up."
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
