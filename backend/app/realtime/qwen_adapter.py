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
        self._ws = await websockets.connect(url, additional_headers=headers)
        self._reader_task = asyncio.create_task(self._read_loop())

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
        deadline = asyncio.get_event_loop().time() + 15.0
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
            if event.get("type") == "session.updated":
                return

    async def append_audio(self, payload_b64: str) -> None:
        await self._send(
            {"type": "input_audio_buffer.append", "audio": payload_b64}
        )

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
    """Deterministic offline tutor that still flows audio + board events.

    Generates a few chalk-text board events via the registry-driven path is the
    hub's job; here we only emit audio deltas + a transcript so the full
    client->server->client pipeline can be exercised end to end.
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._started = False
        self._rng = random.Random(42)

    async def connect(self, instructions: str, tools: list[dict[str, Any]]) -> None:
        self._started = True
        logger.info("FakeQwenSession connected (no API key)")

    async def append_audio(self, payload_b64: str) -> None:
        # Student spoke; after a short pause, the tutor replies.
        asyncio.create_task(self._reply_after_student_speech())

    async def create_response(self) -> None:
        # Explicit client turn-end: tutor replies now.
        asyncio.create_task(self._reply_after_student_speech(delay=0.0))

    async def cancel_response(self) -> None:
        # Best-effort: drop any in-flight reply.
        try:
            while True:
                self._queue.get_nowait()
        except asyncio.QueueEmpty:
            pass

    async def function_call_output(self, call_id: str, output: str) -> None:
        # Tool result acknowledged; continue speaking (no-op in fake).
        logger.debug("fake function_call_output %s -> %s", call_id, output[:80])

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

    # -- internals -------------------------------------------------------

    async def _reply_after_student_speech(self, delay: float = 0.8) -> None:
        if self._started is False:
            return
        await asyncio.sleep(delay)
        lines = [
            "Great question. Let me write that out on the board so we can look at it together.",
            "Here is the key idea: the rate of change tells us how fast the output moves as the input changes.",
            "Written as a derivative, that is d y over d x — the slope of the tangent line at any point.",
            "Try it yourself: what do you think happens when x equals zero?",
            "Well done. You've grasped the core idea. Shall we move to the next concept?",
        ]
        line = self._rng.choice(lines)
        await self._emit_transcript(line, speaker="tutor")
        # Emit a few audio delta frames (silence is fine; client just plays them).
        pcm = bytes(320)  # 20 ms of silence at 16 kHz mono PCM16
        for _ in range(3):
            await asyncio.sleep(0.06)
            await self._queue.put(
                {
                    "type": "response.audio.delta",
                    "delta": base64.b64encode(pcm).decode("ascii"),
                    "sequence": self._rng.randint(0, 10_000),
                }
            )
        await self._queue.put({"type": "response.audio.done", "response_id": "fake-" + str(self._rng.randint(0, 9999))})

    async def _emit_transcript(self, text: str, speaker: str = "tutor") -> None:
        now = datetime.now(timezone.utc).isoformat()
        await self._queue.put(
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "speaker": speaker,
                "transcript": text,
                "started_at": now,
                "ended_at": now,
            }
        )


__all__ = ["QwenRealtimeSession", "FakeQwenSession"]
