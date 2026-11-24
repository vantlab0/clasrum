"""Qwen Realtime transport: real DashScope WebSocket + a deterministic fake.

Authority: ``docs/CONTRACTS.md`` §6 ("Qwen Realtime wire facts").

The real adapter frames messages to DashScope's realtime API and consumes
server events. The fake adapter (`FakeQwenSession`) replays a deterministic
mini-lesson so UI work, demos and CI never need an API key.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import random
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any

import websockets

from app.config import get_settings

logger = logging.getLogger(__name__)

REALTIME_URL = get_settings().qwen_realtime_url
MODEL = get_settings().qwen_model
VOICE = get_settings().qwen_voice
INPUT_RATE = get_settings().qwen_input_sample_rate
OUTPUT_RATE = get_settings().qwen_output_sample_rate

# ---------------------------------------------------------------------------
# real DashScope transport
# ---------------------------------------------------------------------------


class QwenRealtimeSession:
    """Real Qwen Realtime session over DashScope WebSocket."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._ws: websockets.ClientConnection | None = None
        self._queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._reader_task: asyncio.Task[None] | None = None

    async def connect(self, instructions: str, tools: list[dict[str, Any]]) -> None:
        settings = get_settings()
        url = f"{REALTIME_URL}?model={MODEL}"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "X-DashScope-DataInspection": "disable",
        }
        handshake_timeout = max(1.0, float(getattr(settings, "qwen_handshake_timeout_seconds", 20.0)))
        try:
            self._ws = await asyncio.wait_for(
                websockets.connect(url, additional_headers=headers),
                timeout=handshake_timeout,
            )
        except Exception:
            await self.aclose()
            raise

        self._reader_task = asyncio.create_task(self._read_loop())

        try:
            await self._send(
                {
                    "type": "session.update",
                    "session": {
                        "instructions": instructions,
                        "modalities": ["audio", "text"],
                        "voice": VOICE,
                        "input_audio_format": "pcm",
                        "output_audio_format": "pcm",
                        "turn_detection": {
                            "type": settings.qwen_turn_detection,
                            "threshold": 0.5,
                            "silence_duration_ms": 800,
                        },
                        "input_audio_transcription": {
                            "model": "qwen3-asr-flash-realtime",
                        },
                        "tools": tools,
                    },
                }
            )
            # Wait for session.updated (readiness) with a timeout.
            deadline = asyncio.get_event_loop().time() + handshake_timeout
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    raise TimeoutError("Qwen session.update readiness timed out")
                try:
                    event = await asyncio.wait_for(self._queue.get(), timeout=remaining)
                except asyncio.TimeoutError:
                    raise TimeoutError("Qwen session.update readiness timed out")
                if event is None:
                    raise ConnectionError("Qwen WebSocket closed before session.updated")
                etype = event.get("type")
                if etype == "session.updated":
                    return
                if etype == "error":
                    err = event.get("error") if isinstance(event.get("error"), dict) else {}
                    msg = err.get("message") or event.get("message") or "Qwen realtime error"
                    code = err.get("code") or event.get("code")
                    detail = f"{code}: {msg}" if code else str(msg)
                    raise ConnectionError(f"Qwen error during handshake: {detail}")
                # Ignore other events during handshake (e.g. session.created).
        except Exception:
            await self.aclose()
            raise

    async def append_audio(self, payload_b64: str) -> None:
        await self._send(
            {"type": "input_audio_buffer.append", "audio": payload_b64}
        )

    async def commit_audio(self) -> None:
        await self._send({"type": "input_audio_buffer.commit"})

    async def create_response(self) -> None:
        await self._send({"type": "response.create"})

    async def cancel_response(self) -> None:
        await self._send({"type": "response.cancel"})

    async def function_call_output(self, call_id: str, output: str) -> None:
        await self._send(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": output,
                },
            }
        )
        await self.create_response()

    async def prompt_text(self, text: str) -> None:
        await self._send(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": text}],
                },
            }
        )
        await self.create_response()

    async def iterate(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            event = await self._queue.get()
            if event is None:
                return
            yield event

    async def aclose(self) -> None:
        if self._reader_task is not None:
            self._reader_task.cancel()
        if self._ws is not None:
            await self._ws.close()

    # -- internals -------------------------------------------------------

    async def _send(self, payload: dict[str, Any]) -> None:
        if self._ws is None:
            raise RuntimeError("Qwen session not connected")
        await self._ws.send(json.dumps(payload))

    async def _read_loop(self) -> None:
        assert self._ws is not None
        try:
            async for message in self._ws:
                event = json.loads(message)
                await self._queue.put(event)
        except Exception as exc:  # pragma: no cover - transport errors
            logger.warning("Qwen read loop ended: %s", exc)
        finally:
            await self._queue.put(None)


# ---------------------------------------------------------------------------
# fake transport (dev / demo / CI, no API key required)
# ---------------------------------------------------------------------------


class FakeQwenSession:
    """Deterministic offline tutor that flows audio + board events through the real pipeline.

    It simulates Qwen Realtime by emitting response lifecycle events and
    board_intent tool calls. Board events are still produced by the real
    BoardIntentHandler/BoardService pipeline.
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._started = False
        self._rng = random.Random(42)
        self._board_calls = 0
        self._response_seq = 0

    async def connect(self, instructions: str, tools: list[dict[str, Any]]) -> None:
        self._started = True
        logger.info("FakeQwenSession connected (no API key)")

    async def append_audio(self, payload_b64: str) -> None:
        asyncio.create_task(self._reply_after_student_speech())

    async def commit_audio(self) -> None:
        return None

    async def create_response(self) -> None:
        asyncio.create_task(self._reply_after_student_speech(delay=0.0, include_board=False))

    async def cancel_response(self) -> None:
        try:
            while True:
                self._queue.get_nowait()
        except asyncio.QueueEmpty:
            pass

    async def function_call_output(self, call_id: str, output: str) -> None:
        logger.debug("fake function_call_output %s -> %s", call_id, output[:80])
        # Simulate Qwen continuing after tool output.
        asyncio.create_task(self._reply_after_student_speech(delay=0.1, include_board=False))

    async def prompt_text(self, text: str) -> None:
        logger.info("fake prompt_text: %s", text[:120])
        include_board = self._board_calls == 0 or "board_intent" in text.lower() or "heading" in text.lower()
        asyncio.create_task(self._reply_after_student_speech(delay=0.2, include_board=include_board))

    async def iterate(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            event = await self._queue.get()
            if event is None:
                return
            yield event

    async def aclose(self) -> None:
        try:
            self._queue.put_nowait(None)
        except asyncio.QueueFull:
            pass

    async def _reply_after_student_speech(self, delay: float = 0.8, include_board: bool = False) -> None:
        if not self._started:
            return
        await asyncio.sleep(delay)
        self._response_seq += 1
        response_id = f"fake_resp_{self._response_seq}"
        await self._queue.put({"type": "response.created", "response": {"id": response_id, "status": "in_progress"}})

        if include_board:
            self._board_calls += 1
            call_id = f"fake_call_{self._board_calls}"
            intent = (
                "Create a lesson heading and first concept visual for the current topic. "
                "Include the lesson title, a short introduction, the first key concept heading, "
                "one clear definition or explanation, and a simple relevant diagram."
            )
            await self._queue.put(
                {
                    "type": "response.output_item.added",
                    "response_id": response_id,
                    "item": {"type": "function_call", "call_id": call_id, "name": "board_intent"},
                }
            )
            await self._queue.put(
                {
                    "type": "response.function_call_arguments.done",
                    "response_id": response_id,
                    "call_id": call_id,
                    "name": "board_intent",
                    "arguments": json.dumps({"intent": intent, "focus": "new_content", "region_hint": "center"}),
                }
            )
            # The hub will execute the tool and call function_call_output, which triggers a follow-up response.
            return

        line = self._rng.choice([
            "Great question. Let me write that out on the board so we can look at it together.",
            "Here is the key idea: the rate of change tells us how fast the output moves as the input changes.",
            "Written as a derivative, that is d y over d x — the slope of the tangent line at any point.",
            "Try it yourself: what do you think happens when x equals zero?",
            "Well done. You've grasped the core idea. Shall we move to the next concept?",
        ])
        await self._emit_transcript(line, speaker="tutor")
        pcm = bytes(320)
        for i in range(3):
            await asyncio.sleep(0.06)
            await self._queue.put(
                {
                    "type": "response.audio.delta",
                    "response_id": response_id,
                    "delta": base64.b64encode(pcm).decode("ascii"),
                    "sequence": i,
                }
            )
        await self._queue.put({"type": "response.audio.done", "response_id": response_id})
        await self._queue.put({"type": "response.done", "response": {"id": response_id, "status": "completed"}})

    async def _emit_transcript(self, text: str, speaker: str = "tutor") -> None:
        now = datetime.now(timezone.utc).isoformat()
        await self._queue.put(
            {
                "type": "response.audio_transcript.done",
                "speaker": speaker,
                "transcript": text,
                "started_at": now,
                "ended_at": now,
            }
        )


__all__ = ["QwenRealtimeSession", "FakeQwenSession"]
