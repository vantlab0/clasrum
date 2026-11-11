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
import uuid
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
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
from app.db.models import Lesson
from app.db.session import session_scope
from app.graph import service as graph_service
from app.lessons import service as lessons_service
from app.lessons.board_reads import get_board_snapshot, get_transcript_cursor
from app.lessons.transcripts import append_transcript_entry

logger = logging.getLogger(__name__)

# Close codes (docs/CONTRACTS.md §6).
CLOSE_SUPERSEDED = 4001  # newer socket took the lease
CLOSE_FORBIDDEN = 4003  # not the lesson owner
CLOSE_LESSON_ENDED = 4004
CLOSE_QWEN_UNAVAILABLE = 4010

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
MSG_SESSION_ENDED = "session.ended"
MSG_ERROR = "error"
MSG_PONG = "pong"

CLIENT_AUDIO_START = "audio.start"
CLIENT_AUDIO_STOP = "audio.stop"
CLIENT_SESSION_INTERRUPT = "session.interrupt"
CLIENT_SESSION_END = "session.end"
CLIENT_BOARD_REQUEST_SNAPSHOT = "board.request_snapshot"
CLIENT_BOARD_BACKFILL = "board.backfill"
CLIENT_PING = "ping"


@runtime_checkable
class QwenSession(Protocol):
    """A Qwen Realtime transport (real DashScope WS or fake dev transport)."""

    async def connect(self, instructions: str, tools: list[dict[str, Any]]) -> None:
        ...

    async def append_audio(self, payload_b64: str) -> None:
        ...

    async def create_response(self) -> None:
        ...

    async def cancel_response(self) -> None:
        ...

    async def function_call_output(
        self, call_id: str, output: str
    ) -> None:
        ...

    async def iterate(self) -> AsyncIterator[dict[str, Any]]:
        """Yield server-side Qwen events (audio deltas, transcripts, tool calls, ...)."""

    async def aclose(self) -> None:
        ...


@dataclass
class LessonSocket:
    """One live client socket for a lesson."""

    websocket: WebSocket
    lesson_id: str
    student_id: str
    lease_id: str
    applied_sequence: int = 0
    qwen: QwenSession | None = None
    qwen_task: asyncio.Task[None] | None = None
    closed: asyncio.Event = field(default_factory=asyncio.Event)


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

    # -- wiring -----------------------------------------------------------

    def set_board_service(self, board: BoardService) -> None:
        self._board = board

    def set_qwen_factory(self, factory: type[QwenSession]) -> None:
        self._qwen_factory = factory

    # -- BoardEventPublisher / LessonStatePublisher ----------------------

    async def publish_board_event(self, lesson_id: str, event: dict[str, Any]) -> None:
        """Publish a committed board event to the live socket (best-effort)."""
        socket = await self._active_socket(lesson_id)
        if socket is None:
            return  # no live client; event is still persisted
        try:
            await socket.websocket.send_json(
                {MSG_TYPE: MSG_BOARD_EVENT, "event": event}
            )
        except Exception:  # pragma: no cover - a dead socket must not roll back
            logger.debug(
                "board event publish failed (socket gone) lesson=%s seq=%s",
                lesson_id,
                event.get("sequence"),
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
            logger.debug("lesson state publish failed lesson=%s", lesson_id)

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
            logger.debug("lesson event publish failed lesson=%s", lesson_id)

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
                    logger.debug("failed to close superseded socket lesson=%s", lesson_id)
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
                detail = await lessons_service.build_lesson_detail(db, lesson)
                board_envelope = await get_board_snapshot(db, lesson, include_events=False)
                cursor = await get_transcript_cursor(db, lesson.id)

            # Open Qwen Realtime (real or fake).
            settings = get_settings()
            qwen = self._qwen_factory() if self._qwen_factory else _FakeQwenSession()
            socket.qwen = qwen
            await qwen.connect(
                instructions=_teacher_instructions(detail),
                tools=_openai_tools(),
            )

            # Send session.ready per §6.
            await socket.websocket.send_json(
                {
                    MSG_TYPE: MSG_SESSION_READY,
                    "lesson_id": lesson_id,
                    "student_id": student.id,
                    "lease_id": socket.lease_id,
                    "board": board_envelope,
                    "transcript_cursor": cursor,
                    "lesson": detail,
                    "audio": {
                        "input_sample_rate": settings.qwen_input_sample_rate,
                        "output_sample_rate": settings.qwen_output_sample_rate,
                        "format": "pcm16",
                        "channels": 1,
                    },
                }
            )

            # Bridge Qwen -> client in the background.
            socket.qwen_task = asyncio.create_task(self._qwen_loop(socket, db, lesson_id))

            # Client -> Qwen loop.
            await self._client_loop(socket, db, lesson_id)
        except WebSocketDisconnect:
            pass
        finally:
            await self.disconnect(socket)

    async def disconnect(self, socket: LessonSocket) -> None:
        if socket.qwen_task is not None:
            socket.qwen_task.cancel()
        if socket.qwen is not None:
            try:
                await socket.qwen.aclose()
            except Exception:  # pragma: no cover
                logger.debug("qwen close failed lesson=%s", socket.lesson_id)
        socket.closed.set()
        async with self._lock:
            self._all.setdefault(socket.lesson_id, set()).discard(socket)
            if self._sockets.get(socket.lesson_id) is socket:
                self._sockets.pop(socket.lesson_id, None)

    # -- loops -----------------------------------------------------------

    async def _client_loop(
        self,
        socket: LessonSocket,
        db: AsyncSession,
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
                logger.debug("client receive failed lesson=%s", lesson_id, exc_info=True)
                break

            if message.get("type") == "websocket.receive":
                text = message.get("text")
                bytes_data = message.get("bytes")
                if bytes_data is not None:
                    # Raw PCM16 mono 16 kHz -> Qwen input_audio_buffer.append.
                    import base64

                    b64 = base64.b64encode(bytes_data).decode("ascii")
                    try:
                        await socket.qwen.append_audio(b64)
                    except Exception:  # pragma: no cover
                        logger.debug("qwen append_audio failed lesson=%s", lesson_id)
                    continue
                if text is not None:
                    await self._handle_client_text(socket, db, lesson_id, text)
                    continue

        # End of client stream: signal Qwen we're done sending audio for this turn.
        try:
            if socket.qwen is not None:
                await socket.qwen.create_response()
        except Exception:  # pragma: no cover
            logger.debug("qwen create_response on end failed lesson=%s", lesson_id)

    async def _handle_client_text(
        self,
        socket: LessonSocket,
        db: AsyncSession,
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
            # Client signals mic is open; no server action needed beyond logging.
            logger.debug("audio.start lesson=%s", lesson_id)
            return

        if msg_type == CLIENT_AUDIO_STOP:
            # Client finished speaking; ask Qwen to produce a response.
            try:
                if socket.qwen is not None:
                    await socket.qwen.create_response()
            except Exception:  # pragma: no cover
                logger.debug("qwen create_response failed lesson=%s", lesson_id)
            return

        if msg_type == CLIENT_SESSION_INTERRUPT:
            # Barge-in: cancel Qwen response, truncate playback.
            try:
                if socket.qwen is not None:
                    await socket.qwen.cancel_response()
            except Exception:  # pragma: no cover
                logger.debug("qwen cancel_response failed lesson=%s", lesson_id)
            try:
                await socket.websocket.send_json(
                    {MSG_TYPE: MSG_AUDIO_TRUNCATED, "response_id": None, "played_ms": 0}
                )
            except Exception:  # pragma: no cover
                pass
            return

        if msg_type == CLIENT_SESSION_END:
            reason = str(envelope.get("reason") or "student")
            await self._end_lesson(socket, db, lesson_id, reason)
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
                    {MSG_TYPE: MSG_BOARD_SNAPSHOT, "board": envelope_board}
                )
            except Exception as exc:  # pragma: no cover
                logger.debug("snapshot request failed lesson=%s", lesson_id, exc_info=True)
                await _send_error(socket, "internal_error", "could not produce snapshot")
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
                        "events": events,
                        "from_sequence": after_sequence + 1,
                        "to_sequence": after_sequence + len(events),
                    }
                )
            except Exception as exc:  # pragma: no cover
                logger.debug("backfill failed lesson=%s", lesson_id, exc_info=True)
                await _send_error(socket, "internal_error", "could not backfill events")
            return

        await _send_error(socket, "invalid_arguments", f"unknown client message type {msg_type!r}")

    async def _qwen_loop(
        self,
        socket: LessonSocket,
        db: AsyncSession,
        lesson_id: str,
    ) -> None:
        """Bridge Qwen server events -> client + tool call execution."""
        assert socket.qwen is not None
        async for event in socket.qwen.iterate():
            if socket.closed.is_set():
                break
            etype = str(event.get("type") or "")

            if etype == "error":
                await _send_error(socket, "qwen_unavailable", str(event.get("message") or "Qwen error"), retryable=True)
                continue

            if etype == "response.audio.delta":
                await socket.websocket.send_json(
                    {
                        MSG_TYPE: MSG_AUDIO_DELTA,
                        "data": event.get("delta", ""),
                        "sample_rate": get_settings().qwen_output_sample_rate,
                        "sequence": event.get("sequence"),
                    }
                )
                continue

            if etype == "response.audio.done":
                await socket.websocket.send_json({MSG_TYPE: MSG_AUDIO_DONE, "response_id": event.get("response_id")})
                continue

            if etype in {
                "conversation.item.input_audio_transcription.completed",
                "response.audio_transcript.delta",
            }:
                entry = self._transcript_entry_from_qwen(lesson_id, event)
                if entry is not None:
                    await self._persist_transcript(db, lesson_id, entry)
                    await socket.websocket.send_json({MSG_TYPE: MSG_TRANSCRIPT_ENTRY, "entry": entry})
                continue

            if etype == "response.function_call_arguments.done":
                await self._handle_qwen_tool_call(socket, db, lesson_id, event)
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
        db: AsyncSession,
        lesson_id: str,
        event: dict[str, Any],
    ) -> None:
        """Execute a Qwen tool call via the registry and return the result."""
        from app.board.registry import ToolRegistry

        call_id = event.get("call_id")
        name = event.get("name")
        arguments = event.get("arguments", "{}")
        if not call_id or not name:
            return

        registry = ToolRegistry(board=self._board or BoardService())
        registry.set_lesson_publisher(self)
        try:
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

        try:
            if socket.qwen is not None:
                await socket.qwen.function_call_output(call_id, json.dumps(result))
        except Exception:  # pragma: no cover
            logger.debug("qwen function_call_output failed lesson=%s", lesson_id)

    # -- helpers ----------------------------------------------------------

    async def _active_socket(self, lesson_id: str) -> LessonSocket | None:
        async with self._lock:
            return self._sockets.get(lesson_id)

    async def _end_lesson(
        self,
        socket: LessonSocket,
        db: AsyncSession,
        lesson_id: str,
        reason: str,
    ) -> None:
        """End the lesson (graph finalize + board snapshot) and notify client."""
        try:
            async with session_scope() as fresh_db:
                lesson = await fresh_db.get(Lesson, lesson_id)
                if lesson is None:
                    return
                lesson = await graph_service.finish_lesson(fresh_db, lesson_id, reason)
                detail = await lessons_service.build_lesson_detail(fresh_db, lesson)
                board_envelope = await get_board_snapshot(
                    fresh_db, lesson, include_events=False
                )
            await socket.websocket.send_json(
                {
                    MSG_TYPE: MSG_SESSION_ENDED,
                    "reason": reason,
                    "needs_notes_prompt": True,
                    "lesson": detail,
                    "board": board_envelope,
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

    async def _persist_transcript(
        self, db: AsyncSession, lesson_id: str, entry: dict[str, Any]
    ) -> None:
        try:
            started_at = entry.get("started_at")
            ended_at = entry.get("ended_at")
            from datetime import datetime

            started = datetime.fromisoformat(started_at) if started_at else None
            ended = datetime.fromisoformat(ended_at) if ended_at else None
            await append_transcript_entry(
                db,
                lesson_id,
                entry["speaker"],
                entry["text"],
                started_at=started,
                ended_at=ended,
            )
        except Exception:  # pragma: no cover - transcript must not block audio
            logger.debug("transcript persist failed lesson=%s", lesson_id, exc_info=True)


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


async def _lesson_detail(lesson_id: str) -> dict[str, Any]:
    async with session_scope() as db:
        lesson = await db.get(Lesson, lesson_id)
        if lesson is None:
            return {}
        return await lessons_service.build_lesson_detail(db, lesson)


def _teacher_instructions(detail: Mapping[str, Any]) -> str:
    """Build the Qwen teacher instructions from the lesson plan/state."""
    topic = str(detail.get("topic") or "the topic")
    subject = str(detail.get("subject") or "General Studies")
    level = str(detail.get("learning_level") or "beginner")
    concepts = detail.get("plan", {}).get("concepts", []) if isinstance(detail.get("plan"), dict) else []
    concept_list = ", ".join(str(c.get("title", "")) for c in concepts) if concepts else "the lesson concepts"
    return (
        f"You are a patient, encouraging private tutor teaching '{topic}' ({subject}) "
        f"at {level} level. You speak out loud while writing on a shared blackboard. "
        f"Concepts to cover: {concept_list}. "
        f"Rules: (1) Always write key points, definitions and equations on the board "
        f"using the board_* tools before explaining them aloud. (2) Ask the student "
        f"questions and check answers before advancing. (3) Never modify database "
        f"state directly — only through the exposed tools. (4) Keep explanations "
        f"concise and check understanding frequently. (5) Use lesson_finish when "
        f"the student is done or time is up."
    )


def _openai_tools() -> list[dict[str, Any]]:
    from app.board.registry import ToolRegistry

    return ToolRegistry().openai_tools()


__all__ = [
    "LessonRealtimeHub",
    "LessonSocket",
    "QwenSession",
    "CLOSE_SUPERSEDED",
    "CLOSE_FORBIDDEN",
    "CLOSE_LESSON_ENDED",
    "CLOSE_QWEN_UNAVAILABLE",
]
