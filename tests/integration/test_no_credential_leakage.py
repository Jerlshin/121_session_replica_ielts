"""Credential isolation regression test (Spec 01 §8, Spec 04 §2 Phase 8):
"credentials never reach the browser." Reading gemini_bridge.py/ws_exam.py
confirms `settings.gemini_api_key` is only ever used to build the outbound
Gemini WS URL server-side, never included in any client-facing payload —
this test turns that one-time read into an enforced invariant by driving a
real PTT turn with distinctive dummy secrets loaded and asserting none of
them appear in any WS text frame the client receives.

All four vendor keys named in Spec 01 §8 (Gemini, Deepgram, Azure, the LLM
Judge) are set here, even though only `gemini_api_key` lives on
api-gateway's own Settings and is reachable from the WS code path this
test exercises — apps/worker's three keys (deepgram/azure/openai) are
never loaded into this process at all today, so they can't leak via this
path structurally. They're included anyway so this test still catches a
future regression where a worker-side settings import gets wired into the
gateway inadvertently.
"""
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "apps" / "api-gateway"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "apps" / "worker"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "support"))

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from fake_gemini_live_server import FakeGeminiLiveServerHandle  # noqa: E402

FIXTURE = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "gemini_live_replay"
    / "connectivity_test_session.json"
)

_DUMMY_SECRETS = {
    "gemini_api_key": "SECRET-GEMINI-do-not-leak-3f9a",
    "deepgram_api_key": "SECRET-DEEPGRAM-do-not-leak-71bd",
    "azure_speech_key": "SECRET-AZURE-do-not-leak-c204",
    "openai_api_key": "SECRET-OPENAI-do-not-leak-88e1",
}


def _sine_wave_pcm16_frame(num_samples: int = 320, amplitude: int = 3000) -> bytes:
    samples = [int(amplitude * ((i % 32) / 32.0 - 0.5)) for i in range(num_samples)]
    return struct.pack(f"<{num_samples}h", *samples)


def test_no_vendor_secret_appears_in_any_ws_frame():
    from config import settings as worker_settings  # apps/worker's own Settings

    fake = FakeGeminiLiveServerHandle(FIXTURE).start()
    original = {
        "gateway_api_key": settings.gemini_api_key,
        "gateway_ws_url": settings.gemini_live_ws_url,
        "intro_turns": settings.intro_turns,
        "worker_deepgram": worker_settings.deepgram_api_key,
        "worker_azure": worker_settings.azure_speech_key,
        "worker_openai": worker_settings.openai_api_key,
    }

    settings.gemini_api_key = _DUMMY_SECRETS["gemini_api_key"]
    settings.gemini_live_ws_url = fake.url
    settings.intro_turns = 1_000_000
    worker_settings.deepgram_api_key = _DUMMY_SECRETS["deepgram_api_key"]
    worker_settings.azure_speech_key = _DUMMY_SECRETS["azure_speech_key"]
    worker_settings.openai_api_key = _DUMMY_SECRETS["openai_api_key"]

    captured_text_frames: list[str] = []

    try:
        with TestClient(app) as client:
            login_response = client.post(
                "/auth/login",
                json={"email": "leakage-check@example.com", "full_name": "Leakage Tester"},
            )
            token = login_response.json()["access_token"]

            session_response = client.post(
                "/sessions", headers={"Authorization": f"Bearer {token}"}
            )
            session_id = session_response.json()["id"]

            with client.websocket_connect(f"/ws/exam/{session_id}?token={token}") as ws:
                assert ws.receive_json()["type"] == "connected"

                for _ in range(3):
                    event = ws.receive()
                    if "text" in event:
                        captured_text_frames.append(event["text"])

                ws.send_json({"type": "activity_start"})
                ack = ws.receive_json()
                captured_text_frames.append(json.dumps(ack))
                assert ack["type"] == "activity_start_ack"

                for _ in range(5):
                    ws.send_bytes(_sine_wave_pcm16_frame())
                ws.send_json({"type": "activity_end"})

                for _ in range(4):
                    event = ws.receive()
                    if "text" in event:
                        captured_text_frames.append(event["text"])

            # /metrics is also client-reachable (Spec 04 §2 Phase 8) —
            # covered by the same invariant.
            captured_text_frames.append(client.get("/metrics").text)
            captured_text_frames.append(client.get("/healthz").text)

    finally:
        settings.gemini_api_key = original["gateway_api_key"]
        settings.gemini_live_ws_url = original["gateway_ws_url"]
        settings.intro_turns = original["intro_turns"]
        worker_settings.deepgram_api_key = original["worker_deepgram"]
        worker_settings.azure_speech_key = original["worker_azure"]
        worker_settings.openai_api_key = original["worker_openai"]
        fake.stop()

    all_text = "\n".join(captured_text_frames)
    for name, secret in _DUMMY_SECRETS.items():
        assert secret not in all_text, f"{name} leaked into a client-facing payload"
