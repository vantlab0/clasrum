"""Realtime WebSocket router: ``GET /api/v1/ws/lessons/{lesson_id}?token=<jwt>``.

Authority: ``docs/CONTRACTS.md`` §6.

Mounted by ``app.main`` under ``settings.api_prefix`` so the full path is
``/api/v1/ws/lessons/{lesson_id}``.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status

from app.core import errors
from app.core.deps import resolve_student
from app.db.models import Lesson
from app.db.session import session_scope
from app.realtime.hub import (
    CLOSE_FORBIDDEN,
    LessonRealtimeHub,
)
from app.realtime.qwen_adapter import FakeQwenSession, QwenRealtimeSession

logger = logging.getLogger(__name__)

router = APIRouter(tags=["realtime"])

# Module-level hub singleton shared by the router and the board service.
_hub: LessonRealtimeHub | None = None


def get_hub() -> LessonRealtimeHub:
    global _hub
    if _hub is None:
        _hub = LessonRealtimeHub()
    return _hub


def configure_hub(
    board_service=None, qwen_factory=None
) -> LessonRealtimeHub:
    """Wire the hub into the board service and Qwen transport factory."""
    hub = get_hub()
    if board_service is not None:
        hub.set_board_service(board_service)
    if qwen_factory is not None:
        hub.set_qwen_factory(qwen_factory)
    return hub


@router.websocket("/lessons/{lesson_id}")
async def lessons_ws(
    lesson_id: str,
    token: str | None = Query(default=None, description="JWT access token"),
    websocket: WebSocket = None,  # type: ignore[assignment]
) -> None:
    """WebSocket endpoint for a live lesson.

    Auth is via the ``token`` query param (browsers cannot set WS headers).
    Ownership is resolved server-side; the client never supplies a student id.
    """
    hub = get_hub()

    # Authenticate + resolve ownership before accepting the socket, so a failed
    # auth can return a clean close code instead of half-open state.
    async with session_scope() as db:
        try:
            student = await resolve_student(db, token)
        except errors.ApiError:
            await websocket.close(code=CLOSE_FORBIDDEN)
            return
        lesson = await db.get(Lesson, lesson_id)
        if lesson is None:
            await websocket.close(code=CLOSE_FORBIDDEN)
            return
        if lesson.student_id != student.id:
            await websocket.close(code=CLOSE_FORBIDDEN)
            return

    # Choose Qwen transport: real when an API key is configured, fake otherwise.
    settings = __import__("app.config", fromlist=["get_settings"]).get_settings()
    if settings.qwen_api_key and not settings.qwen_fake_mode:
        qwen_factory = lambda: QwenRealtimeSession(settings.qwen_api_key)  # noqa: E731
    else:
        qwen_factory = FakeQwenSession

    hub = configure_hub(qwen_factory=qwen_factory)

    await hub.connect(websocket, lesson_id, token)


__all__ = ["router", "configure_hub", "get_hub"]
