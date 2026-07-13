"""Exercises the Phase 2 exit criterion end-to-end through the real FastAPI
`/ws/exam/{session_id}` gateway (Spec 04 §2): PTT press/speak/release must
produce a relayed Gemini audio reply, and the candidate's own turn audio
must still land in object storage with correct checksum metadata (Spec 01
§4.2/§5.5/§7) — now via a background flush that doesn't block the relay.

Runs against a fake local Gemini Live server replaying a recorded fixture
(Spec 04 §3), so this test never dials out to the real Google API. Requires
the dev stack (Postgres + MinIO) running.
"""
import asyncio
import hashlib
import io
import json
import struct
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "apps" / "api-gateway"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.models import ExamSession  # noqa: E402
from app.services.media_tap import get_s3_client  # noqa: E402
from _fake_gemini_live_server import FakeGeminiLiveServerHandle  # noqa: E402

FIXTURE = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "gemini_live_replay"
    / "connectivity_test_session.json"
)


def _sine_wave_pcm16_frame(num_samples: int = 320, amplitude: int = 3000) -> bytes:
    """320 samples = one 20ms frame at 16kHz (Spec 01 §4.1)."""
    samples = [int(amplitude * ((i % 32) / 32.0 - 0.5)) for i in range(num_samples)]
    return struct.pack(f"<{num_samples}h", *samples)


def _expected_wav_bytes(pcm_bytes: bytes) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(pcm_bytes)
    return buffer.getvalue()


async def _load_resumption_handle(session_id: str) -> str | None:
    # A standalone engine, not the app's shared `AsyncSessionLocal` — that
    # one is bound to whichever loop the TestClient portal last used, and
    # this helper runs after that portal (and its loop) has already closed.
    engine = create_async_engine(settings.database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as db:
            session = await db.get(ExamSession, session_id)
            return session.gemini_resumption_handle if session else None
    finally:
        await engine.dispose()


def test_ptt_turn_gets_relayed_gemini_reply_and_persists_candidate_audio():
    fake = FakeGeminiLiveServerHandle(FIXTURE).start()
    original_ws_url = settings.gemini_live_ws_url
    original_api_key = settings.gemini_api_key
    settings.gemini_live_ws_url = fake.url
    settings.gemini_api_key = "fixture-unused-key"

    try:
        with TestClient(app) as client:
            login_response = client.post(
                "/auth/login",
                json={"email": "gemini-bridge@example.com", "full_name": "Bridge Tester"},
            )
            token = login_response.json()["access_token"]

            session_response = client.post(
                "/sessions", headers={"Authorization": f"Bearer {token}"}
            )
            session_id = session_response.json()["id"]

            with client.websocket_connect(f"/ws/exam/{session_id}?token={token}") as ws:
                connected = ws.receive_json()
                assert connected == {"type": "connected", "session_id": session_id}

                # The Phase 2 "say hello back" scripted connectivity turn
                # (Spec 04 §2) fires automatically on a fresh connection,
                # before any PTT — no client action needed to trigger it.
                # Frame classification: JSON control messages vs. relayed
                # binary audio; order isn't asserted since it isn't a
                # contract this system makes (only "eventually arrives").
                greeting_events = [ws.receive() for _ in range(3)]
                greeting_types = set()
                for event in greeting_events:
                    if "text" in event:
                        greeting_types.add(json.loads(event["text"])["type"])
                    elif "bytes" in event:
                        greeting_types.add("audio_bytes")
                assert greeting_types == {"transcript_delta", "gemini_turn_complete", "audio_bytes"}

                # Now the candidate's own PTT turn.
                frames = [_sine_wave_pcm16_frame() for _ in range(5)]
                pcm_bytes = b"".join(frames)

                ws.send_json({"type": "activity_start"})
                ack = ws.receive_json()
                assert ack["type"] == "activity_start_ack"
                turn_id = ack["turn_id"]

                for frame in frames:
                    ws.send_bytes(frame)

                ws.send_json({"type": "activity_end"})

                # Expect 4 messages: gemini's transcript_delta, its relayed
                # audio bytes, its gemini_turn_complete, and our own
                # turn_complete (S3 persistence ack) — these race with each
                # other by design (Spec 01 §4.2: media flush is
                # non-blocking), so we bucket rather than assume order.
                reply_events = [ws.receive() for _ in range(4)]

                media_turn_complete = None
                reply_types = set()
                for event in reply_events:
                    if "bytes" in event:
                        reply_types.add("audio_bytes")
                        continue
                    payload = json.loads(event["text"])
                    reply_types.add(payload["type"])
                    if payload["type"] == "turn_complete":
                        media_turn_complete = payload

                assert reply_types == {
                    "transcript_delta",
                    "gemini_turn_complete",
                    "audio_bytes",
                    "turn_complete",
                }
                assert media_turn_complete["turn_id"] == turn_id

        expected_wav = _expected_wav_bytes(pcm_bytes)
        expected_checksum = hashlib.sha256(expected_wav).hexdigest()
        assert media_turn_complete["checksum"] == expected_checksum
        assert media_turn_complete["byte_size"] == len(expected_wav)

        s3 = get_s3_client()
        storage_key = f"raw-audio/{session_id}/segments/{turn_id}_1.wav"
        obj = s3.get_object(Bucket=settings.s3_bucket, Key=storage_key)
        stored_bytes = obj["Body"].read()
        assert stored_bytes == expected_wav

        # The bridge's sessionResumptionUpdate (from the fixture) must have
        # been persisted to the durable session row (Spec 01 §5.3), not
        # just held in memory.
        stored_handle = asyncio.run(_load_resumption_handle(session_id))
        assert stored_handle == "fixture-handle-turn-0002"

    finally:
        settings.gemini_live_ws_url = original_ws_url
        settings.gemini_api_key = original_api_key
        fake.stop()
