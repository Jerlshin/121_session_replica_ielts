"""Protocol-level test for gemini_bridge.py against a recorded fixture
(Spec 04 §2 Phase 2 exit criterion: "a recorded-fixture replay test passes
in CI without hitting the real API"). No FastAPI, no Postgres/MinIO — pure
bridge <-> fake Gemini WS.
"""
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "apps" / "api-gateway"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "support"))

from app.services.gemini_bridge import (  # noqa: E402
    AudioDelta,
    GeminiLiveBridge,
    SessionResumptionUpdate,
    TranscriptDelta,
    TurnComplete,
)
from fake_gemini_live_server import FakeGeminiLiveServer  # noqa: E402

FIXTURE = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "gemini_live_replay"
    / "connectivity_test_session.json"
)


async def test_connect_sends_disabled_vad_and_receives_setup_complete():
    async with FakeGeminiLiveServer(FIXTURE) as fake:
        bridge = GeminiLiveBridge(
            session_id=uuid.uuid4(),
            api_key="fixture-unused-key",
            model_id="models/gemini-2.0-flash-live-001",
            ws_url=fake.url,
            system_instruction="[PERSONA]\ntest persona\n",
        )
        await bridge.connect()
        await bridge.close()

    assert len(fake.received_messages) == 1
    setup = fake.received_messages[0]["setup"]
    # Push-to-Talk overrides automatic VAD (Spec 01 §4.1 / CLAUDE.md rule 2).
    assert setup["realtimeInputConfig"]["automaticActivityDetection"]["disabled"] is True
    assert setup["generationConfig"]["responseModalities"] == ["AUDIO"]
    assert "sessionResumption" not in setup  # fresh session, no handle yet


async def test_directive_injection_yields_scripted_greeting_and_resumption_handle():
    async with FakeGeminiLiveServer(FIXTURE) as fake:
        bridge = GeminiLiveBridge(
            session_id=uuid.uuid4(),
            api_key="fixture-unused-key",
            model_id="models/gemini-2.0-flash-live-001",
            ws_url=fake.url,
            system_instruction="[PERSONA]\ntest persona\n",
        )
        await bridge.connect()
        await bridge.inject_directive("[EXAMINER_DIRECTIVE]\nsay hello back\n[/EXAMINER_DIRECTIVE]")

        events = []
        async for event in bridge.receive_events():
            events.append(event)
            if isinstance(event, SessionResumptionUpdate):
                break

        await bridge.close()

    assert any(isinstance(e, TranscriptDelta) for e in events)
    assert any(isinstance(e, AudioDelta) and len(e.pcm_bytes) > 0 for e in events)
    assert any(isinstance(e, TurnComplete) for e in events)

    resumption_events = [e for e in events if isinstance(e, SessionResumptionUpdate)]
    assert resumption_events[-1].handle == "fixture-handle-connectivity-0001"
    assert resumption_events[-1].resumable is True

    # The clientContent turn the bridge sent must carry our directive text
    # verbatim, wrapped exactly as the base persona expects (Spec 02 §6.2).
    injected = fake.received_messages[1]["clientContent"]
    assert "[EXAMINER_DIRECTIVE]" in injected["turns"][0]["parts"][0]["text"]


async def test_resumption_handle_is_forwarded_on_reconnect():
    async with FakeGeminiLiveServer(FIXTURE) as fake:
        bridge = GeminiLiveBridge(
            session_id=uuid.uuid4(),
            api_key="fixture-unused-key",
            model_id="models/gemini-2.0-flash-live-001",
            ws_url=fake.url,
            system_instruction="[PERSONA]\ntest persona\n",
            resumption_handle="stored-handle-from-a-previous-connection",
        )
        await bridge.connect()
        await bridge.close()

    setup = fake.received_messages[0]["setup"]
    assert setup["sessionResumption"]["handle"] == "stored-handle-from-a-previous-connection"


async def test_force_mute_input_drops_audio_frames():
    async with FakeGeminiLiveServer(FIXTURE) as fake:
        bridge = GeminiLiveBridge(
            session_id=uuid.uuid4(),
            api_key="fixture-unused-key",
            model_id="models/gemini-2.0-flash-live-001",
            ws_url=fake.url,
            system_instruction="[PERSONA]\ntest persona\n",
        )
        await bridge.connect()
        bridge.force_mute_input()
        await bridge.send_audio_frame(b"\x00\x01" * 160)
        await bridge.close()

    # Only the initial setup reached the server — the muted frame never did.
    assert len(fake.received_messages) == 1
