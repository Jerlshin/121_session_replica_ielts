"""Server-side bridge between our WS gateway and Google's Gemini Live API
(`BidiGenerateContent`), speaking the raw WebSocket JSON protocol directly
rather than the `google-genai` SDK — see docs/adr/0001-raw-websocket-gemini-bridge.md
for why.

This module owns only the protocol translation: connect/setup, forwarding
PTT-bounded audio (Spec 01 §4.1 — automatic VAD is disabled; activityStart/
activityEnd are the sole turn boundary, matching CLAUDE.md rule 2),
directive injection, input muting, and decoding server events. It has no
opinion about exam phases, timers, or persistence — those are the caller's
job (Phase 3+ for FSM/timers, `ws_exam.py` for persistence).
"""

from __future__ import annotations

import base64
import json
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import websockets

from app.services.media_tap import PCM_SAMPLE_RATE_HZ as PCM_INPUT_SAMPLE_RATE_HZ

logger = logging.getLogger("app.gemini_bridge")


class GeminiBridgeError(RuntimeError):
    """Raised when the Live API's setup handshake fails or the connection
    closes in a way the caller must not silently ignore."""


# --- Server -> bridge events -------------------------------------------------
# Typed so callers (ws_exam.py) can dispatch without re-parsing raw JSON.


@dataclass(frozen=True)
class AudioDelta:
    """Raw PCM16 audio bytes at settings.gemini_output_sample_rate_hz (24kHz,
    Spec 01 §4.1) — never re-encoded, relayed straight through."""

    pcm_bytes: bytes


@dataclass(frozen=True)
class TranscriptDelta:
    """Gemini's own output_audio_transcription (Spec 01 §4.3) — the live
    caption lane only. Never the scoring source of truth."""

    text: str


@dataclass(frozen=True)
class TurnComplete:
    pass


@dataclass(frozen=True)
class Interrupted:
    """Candidate started a new PTT turn while Gemini was still speaking."""

    pass


@dataclass(frozen=True)
class SessionResumptionUpdate:
    handle: str
    resumable: bool


@dataclass(frozen=True)
class GoAway:
    """The Live API is about to close this connection (Spec 01 §5.6)."""

    time_left_ms: int | None


GeminiEvent = (
    AudioDelta | TranscriptDelta | TurnComplete | Interrupted | SessionResumptionUpdate | GoAway
)


def _parse_go_away_time_left(payload: dict) -> int | None:
    """`timeLeft` is a proto Duration serialized as e.g. "9.5s"; we only need
    a rough millisecond figure for client-facing heads-up, not precision."""
    raw = payload.get("timeLeft")
    if not raw or not isinstance(raw, str) or not raw.endswith("s"):
        return None
    try:
        return int(float(raw[:-1]) * 1000)
    except ValueError:
        return None


class GeminiLiveBridge:
    def __init__(
        self,
        *,
        session_id: uuid.UUID,
        api_key: str,
        model_id: str,
        ws_url: str,
        system_instruction: str,
        resumption_handle: str | None = None,
    ) -> None:
        self._session_id = session_id
        self._api_key = api_key
        self._model_id = model_id
        self._ws_url = ws_url
        self._system_instruction = system_instruction
        self._resumption_handle = resumption_handle

        self._ws: websockets.WebSocketClientProtocol | None = None
        self._input_muted = False

    async def connect(self) -> None:
        url = f"{self._ws_url}?key={self._api_key}"
        self._ws = await websockets.connect(url, max_size=None)

        setup: dict = {
            "setup": {
                "model": self._model_id,
                "generationConfig": {"responseModalities": ["AUDIO"]},
                "systemInstruction": {"parts": [{"text": self._system_instruction}]},
                # Push-to-Talk overrides automatic VAD (Spec 01 §4.1,
                # CLAUDE.md rule 2) — activityStart/activityEnd are sent
                # explicitly by send_activity_start/send_activity_end below.
                "realtimeInputConfig": {"automaticActivityDetection": {"disabled": True}},
                # Live caption lane only (Spec 01 §4.3) — never the scoring
                # source of truth.
                "outputAudioTranscription": {},
            }
        }
        if self._resumption_handle:
            # Handle-based resumption restores server-side context
            # transparently (Spec 01 §5.3) — omitted entirely for a fresh
            # session so Google issues a brand new handle.
            setup["setup"]["sessionResumption"] = {"handle": self._resumption_handle}

        await self._ws.send(json.dumps(setup))

        first_raw = await self._ws.recv()
        first = json.loads(first_raw)
        if "setupComplete" not in first:
            raise GeminiBridgeError(f"Gemini Live setup failed: {first!r}")

    async def send_activity_start(self) -> None:
        await self._send({"realtimeInput": {"activityStart": {}}})

    async def send_activity_end(self) -> None:
        await self._send({"realtimeInput": {"activityEnd": {}}})

    async def send_audio_frame(self, pcm_bytes: bytes) -> None:
        if self._input_muted:
            return
        payload = {
            "realtimeInput": {
                "audio": {
                    "data": base64.b64encode(pcm_bytes).decode("ascii"),
                    "mimeType": f"audio/pcm;rate={PCM_INPUT_SAMPLE_RATE_HZ}",
                }
            }
        }
        await self._send(payload)

    async def inject_directive(self, directive_text: str) -> None:
        """Sends an out-of-band [EXAMINER_DIRECTIVE] turn (Spec 02 §6.2).
        The base persona's conversational rule 1 instructs Gemini to treat
        this as silent stage direction, never spoken or acknowledged."""
        turn = {
            "clientContent": {
                "turns": [{"role": "user", "parts": [{"text": directive_text}]}],
                "turnComplete": True,
            }
        }
        await self._send(turn)

    def force_mute_input(self) -> None:
        """Stops forwarding candidate audio even if PTT is still physically
        held — the Part 2 hard-cutoff primitive (Spec 02 §3.3). Wiring this
        to the timer watchdog is Phase 3's job; the bridge only exposes the
        mechanism."""
        self._input_muted = True

    def unmute_input(self) -> None:
        self._input_muted = False

    async def receive_events(self) -> AsyncIterator[GeminiEvent]:
        if self._ws is None:
            raise GeminiBridgeError("receive_events() called before connect()")

        async for raw in self._ws:
            message = json.loads(raw)

            if "serverContent" in message:
                content = message["serverContent"]

                if content.get("interrupted"):
                    yield Interrupted()

                model_turn = content.get("modelTurn")
                if model_turn:
                    for part in model_turn.get("parts", []):
                        inline_data = part.get("inlineData")
                        if inline_data and "data" in inline_data:
                            yield AudioDelta(base64.b64decode(inline_data["data"]))

                output_transcription = content.get("outputTranscription")
                if output_transcription and output_transcription.get("text"):
                    yield TranscriptDelta(output_transcription["text"])

                if content.get("turnComplete"):
                    yield TurnComplete()

            elif "sessionResumptionUpdate" in message:
                update = message["sessionResumptionUpdate"]
                if update.get("newHandle"):
                    yield SessionResumptionUpdate(
                        handle=update["newHandle"],
                        resumable=bool(update.get("resumable", False)),
                    )

            elif "goAway" in message:
                yield GoAway(time_left_ms=_parse_go_away_time_left(message["goAway"]))

            else:
                logger.debug(
                    "gemini_bridge: unhandled message session=%s keys=%s",
                    self._session_id,
                    list(message.keys()),
                )

    async def close(self) -> None:
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    async def _send(self, payload: dict) -> None:
        if self._ws is None:
            raise GeminiBridgeError("bridge is not connected")
        await self._ws.send(json.dumps(payload))


def load_base_persona(prompt_templates_dir) -> str:
    """Reads the versioned base persona asset (Spec 02 §6.1) rather than
    inlining prompt text in application code (Spec 04 §1)."""
    path = prompt_templates_dir / "base_persona_v1.txt"
    return path.read_text()


def load_directive(prompt_templates_dir, name: str) -> str:
    path = prompt_templates_dir / "directives" / f"{name}.txt"
    return path.read_text()
