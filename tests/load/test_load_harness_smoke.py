"""CI-safe smoke test for live_session_load.py (Spec 04 §2 Phase 8) — spawns
a real gateway subprocess (genuine concurrent TCP connections, unlike
in-process TestClient) pointed at a fake local Gemini server, runs the load
harness at low concurrency for a few seconds, and asserts it completes with
zero errors/dropped connections. This proves the harness mechanism itself
works; it is deliberately NOT a real load test (see live_session_load.py's
module docstring for why CI never drives anywhere near Spec 01 §9's
~1000-session ceiling).
"""

import asyncio
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "support"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_gemini_live_server import FakeGeminiLiveServerHandle  # noqa: E402
from live_session_load import run_load_test  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "gemini_live_replay" / "connectivity_test_session.json"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_until_healthy(base_url: str, timeout_s: float = 20.0) -> None:
    deadline = time.monotonic() + timeout_s
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        try:
            response = httpx.get(f"{base_url}/healthz", timeout=1.0)
            if response.status_code == 200:
                return
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
        time.sleep(0.25)
    raise RuntimeError(f"gateway did not become healthy in time: {last_exc}")


def test_load_harness_completes_with_zero_errors_at_low_concurrency():
    fake = FakeGeminiLiveServerHandle(FIXTURE).start()
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"

    env = {
        **os.environ,
        "GEMINI_LIVE_WS_URL": fake.url,
        "GEMINI_API_KEY": "fixture-unused-key",
        # A huge intro-turn budget keeps every simulated PTT turn inside
        # INTRO — the same trick test_exam_room_gemini_relay.py uses — so
        # the harness only has to drive the Phase 2 relay loop, not the
        # full FSM, for this mechanism-level smoke check.
        "INTRO_TURNS": "1000000",
    }

    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=str(REPO_ROOT / "apps" / "api-gateway"),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    try:
        _wait_until_healthy(base_url)

        report = asyncio.run(
            run_load_test(
                gateway_http_url=base_url,
                gateway_ws_url=f"ws://127.0.0.1:{port}",
                concurrency=5,
                duration_s=4.0,
                turn_min_s=0.1,
                turn_max_s=0.3,
            )
        )

        assert report.setup_errors == 0, report.summary()
        assert report.dropped_connections == 0, report.summary()
        assert report.error_count == 0, report.summary()
        assert report.total_turns > 0, report.summary()

    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        fake.stop()
