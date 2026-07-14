"""Proves observability.py's histograms actually record a sample after a
real PTT turn through the gateway (Spec 01 §4.4, Spec 04 §2 Phase 8) —
same fixture-driven fake-Gemini pattern as
test_exam_room_gemini_relay.py, but scraping `/metrics` afterward instead
of asserting on the WS reply frames themselves. Filters on this test's own
session_id label so it's robust to metric samples left behind by other
tests sharing the same process-wide OTel MeterProvider.
"""
import json
import re
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "apps" / "api-gateway"))
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


def _sine_wave_pcm16_frame(num_samples: int = 320, amplitude: int = 3000) -> bytes:
    samples = [int(amplitude * ((i % 32) / 32.0 - 0.5)) for i in range(num_samples)]
    return struct.pack(f"<{num_samples}h", *samples)


def _metric_count(metrics_text: str, metric_name: str, session_id: str) -> float:
    pattern = re.compile(
        rf'^{re.escape(metric_name)}_count\{{[^}}]*session_id="{re.escape(session_id)}"[^}}]*\}} (\S+)$',
        re.MULTILINE,
    )
    match = pattern.search(metrics_text)
    return float(match.group(1)) if match else 0.0


def test_ptt_turn_records_latency_histograms():
    fake = FakeGeminiLiveServerHandle(FIXTURE).start()
    original_ws_url = settings.gemini_live_ws_url
    original_api_key = settings.gemini_api_key
    original_intro_turns = settings.intro_turns
    settings.gemini_live_ws_url = fake.url
    settings.gemini_api_key = "fixture-unused-key"
    settings.intro_turns = 1_000_000  # keep this to one PTT turn, like the relay test

    try:
        with TestClient(app) as client:
            login_response = client.post(
                "/auth/login",
                json={"email": "observability@example.com", "full_name": "Metrics Tester"},
            )
            token = login_response.json()["access_token"]

            session_response = client.post(
                "/sessions", headers={"Authorization": f"Bearer {token}"}
            )
            session_id = session_response.json()["id"]

            with client.websocket_connect(f"/ws/exam/{session_id}?token={token}") as ws:
                assert ws.receive_json()["type"] == "connected"
                [ws.receive() for _ in range(3)]  # intro directive's reply burst

                ws.send_json({"type": "activity_start"})
                assert ws.receive_json()["type"] == "activity_start_ack"

                for _ in range(5):
                    ws.send_bytes(_sine_wave_pcm16_frame())
                ws.send_json({"type": "activity_end"})

                reply_events = [ws.receive() for _ in range(4)]
                reply_types = {
                    "audio_bytes" if "bytes" in e else json.loads(e["text"])["type"]
                    for e in reply_events
                }
                assert reply_types == {
                    "transcript_delta",
                    "gemini_turn_complete",
                    "audio_bytes",
                    "turn_complete",
                }

            metrics_response = client.get("/metrics")
            assert metrics_response.status_code == 200
            metrics_text = metrics_response.text

        for metric_name in (
            "gateway_to_gemini_send_ms_milliseconds",
            "gemini_response_ms_milliseconds",
            "gateway_relay_enqueue_ms_milliseconds",
            "ptt_release_to_first_audio_ms_milliseconds",
        ):
            assert _metric_count(metrics_text, metric_name, session_id) >= 1, (
                f"{metric_name} did not record a sample for session={session_id}"
            )

    finally:
        settings.gemini_live_ws_url = original_ws_url
        settings.gemini_api_key = original_api_key
        settings.intro_turns = original_intro_turns
        fake.stop()
