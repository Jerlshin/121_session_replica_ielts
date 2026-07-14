"""Concurrency load-testing harness (Spec 01 §9: <=1000 concurrent sessions
per region; Spec 04 §2 Phase 8) — simulates N concurrent candidates, each
running repeated realistic PTT turn cycles against a *running* gateway
process (a real uvicorn process, not FastAPI's in-process TestClient —
genuine concurrent TCP connections are the point), and reports
release->first-audio-byte latency percentiles plus error/drop counts.

THIS SCRIPT ONLY GENERATES LOAD — it does not start the gateway process
itself. Point --gateway-http-url/--gateway-ws-url at an already-running
gateway (e.g. `uvicorn app.main:app --host 0.0.0.0 --port 8000`). By
default you should run that gateway with GEMINI_LIVE_WS_URL overridden to
a fake local Gemini server (tests/support/fake_gemini_live_server.py) and
INTRO_TURNS set very high so simulated PTT turns don't trip real FSM phase
transitions — see test_load_harness_smoke.py for a fully self-contained
example that spawns both the fake Gemini server and a real gateway
subprocess. Passing --real-gemini is a no-op here (this script has no
opinion about the vendor its target gateway is wired to); it's a reminder
flag for whoever's launching this against a gateway that IS pointed at
the real vendor, since that spends real quota.

Default --concurrency is intentionally small (20) — this is meant to run
safely on a dev laptop or CI runner. Spec 01 §9's ~1000-session ceiling is
a real production target, but actually driving 1000 concurrent WS
connections is a manual, opt-in stress run against a properly scaled
deployment, not something this script (or CI) spins up by default.

Known measurement-fidelity limitation: the fixture-driven fake Gemini
server's scripted replies aren't tagged per-request, so if a reanchor
directive (Spec 02 §6.3, injected periodically by exam_orchestrator.py)
fires an extra reply burst overlapping a turn boundary, a later turn can
occasionally read a stray leftover message from an earlier burst before
its own reply arrives, skewing that one latency sample. Acceptable for a
load-generation harness measuring aggregate percentiles across many turns;
not treated as a bug worth deeper protocol correlation to fix.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import random
import struct
import time
import uuid

import httpx
import websockets


def _pcm16_frame(num_samples: int = 320, amplitude: int = 3000) -> bytes:
    """320 samples = one 20ms frame at 16kHz (Spec 01 §4.1)."""
    samples = [int(amplitude * ((i % 32) / 32.0 - 0.5)) for i in range(num_samples)]
    return struct.pack(f"<{num_samples}h", *samples)


@dataclasses.dataclass
class TurnResult:
    latency_ms: float | None  # None if this turn errored/timed out
    error: str | None = None


@dataclasses.dataclass
class SessionResult:
    turns: list[TurnResult] = dataclasses.field(default_factory=list)
    connection_dropped: bool = False
    setup_error: str | None = None


@dataclasses.dataclass
class LoadTestReport:
    concurrency: int
    duration_s: float
    total_turns: int
    error_count: int
    dropped_connections: int
    setup_errors: int
    latencies_ms: list[float]

    def percentile(self, pct: float) -> float | None:
        if not self.latencies_ms:
            return None
        ordered = sorted(self.latencies_ms)
        index = min(len(ordered) - 1, int(len(ordered) * pct / 100))
        return ordered[index]

    def summary(self) -> str:
        lines = [
            f"concurrency={self.concurrency} duration_s={self.duration_s:.1f} "
            f"total_turns={self.total_turns} errors={self.error_count} "
            f"dropped_connections={self.dropped_connections} setup_errors={self.setup_errors}",
        ]
        if self.latencies_ms:
            lines.append(
                f"latency_ms  p50={self.percentile(50):.1f}  p95={self.percentile(95):.1f}  "
                f"p99={self.percentile(99):.1f}  max={max(self.latencies_ms):.1f}  "
                f"(Spec 01 §4.4 targets: p50~380ms, p95~980ms)"
            )
        else:
            lines.append("latency_ms  no successful turns recorded")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "concurrency": self.concurrency,
            "duration_s": self.duration_s,
            "total_turns": self.total_turns,
            "error_count": self.error_count,
            "dropped_connections": self.dropped_connections,
            "setup_errors": self.setup_errors,
            "p50_ms": self.percentile(50),
            "p95_ms": self.percentile(95),
            "p99_ms": self.percentile(99),
            "max_ms": max(self.latencies_ms) if self.latencies_ms else None,
        }


async def _expect_json_type(ws, expected_type: str) -> None:
    while True:
        message = await ws.recv()
        if isinstance(message, str) and json.loads(message).get("type") == expected_type:
            return


async def _drain_initial_burst(ws, *, seconds: float = 1.0) -> None:
    """Absorbs the intro directive's scripted reply burst (transcript_delta
    / gemini_turn_complete / audio bytes) that fires automatically on
    connect, before the first simulated PTT turn — best-effort, bounded by
    a short deadline rather than an exact expected count."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            await asyncio.wait_for(ws.recv(), timeout=max(0.0, deadline - time.monotonic()))
        except asyncio.TimeoutError:
            return


async def _run_one_turn(
    ws, *, turn_min_s: float, turn_max_s: float, per_turn_timeout_s: float
) -> TurnResult:
    turn_seconds = random.uniform(turn_min_s, turn_max_s)
    frame_count = max(1, round(turn_seconds / 0.02))

    await ws.send(json.dumps({"type": "activity_start"}))
    try:
        await asyncio.wait_for(_expect_json_type(ws, "activity_start_ack"), timeout=5)
    except asyncio.TimeoutError:
        return TurnResult(latency_ms=None, error="activity_start_ack timeout")

    for _ in range(frame_count):
        await ws.send(_pcm16_frame())
        await asyncio.sleep(0.02)  # real-time cadence, matching the AudioWorklet's 20ms framing

    release_time = time.monotonic()
    await ws.send(json.dumps({"type": "activity_end"}))

    latency_ms: float | None = None
    seen_turn_complete = False
    seen_gemini_turn_complete = False
    deadline = time.monotonic() + per_turn_timeout_s

    try:
        while time.monotonic() < deadline and not (seen_turn_complete and seen_gemini_turn_complete):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            message = await asyncio.wait_for(ws.recv(), timeout=remaining)
            if isinstance(message, bytes):
                if latency_ms is None:
                    latency_ms = (time.monotonic() - release_time) * 1000
            else:
                payload = json.loads(message)
                if payload.get("type") == "turn_complete":
                    seen_turn_complete = True
                elif payload.get("type") == "gemini_turn_complete":
                    seen_gemini_turn_complete = True
    except asyncio.TimeoutError:
        pass

    if latency_ms is None:
        return TurnResult(latency_ms=None, error="no audio reply before timeout")
    return TurnResult(latency_ms=latency_ms)


async def _run_one_session(
    *,
    gateway_http_url: str,
    gateway_ws_url: str,
    duration_s: float,
    turn_min_s: float,
    turn_max_s: float,
    candidate_index: int,
) -> SessionResult:
    result = SessionResult()
    email = f"load-test-{uuid.uuid4().hex[:10]}-{candidate_index}@example.com"
    token: str | None = None
    session_id: str | None = None

    try:
        async with httpx.AsyncClient(base_url=gateway_http_url, timeout=10.0) as client:
            login = await client.post(
                "/auth/login", json={"email": email, "full_name": "Load Tester"}
            )
            login.raise_for_status()
            token = login.json()["access_token"]

            session_response = await client.post(
                "/sessions", headers={"Authorization": f"Bearer {token}"}
            )
            session_response.raise_for_status()
            session_id = session_response.json()["id"]
    except Exception as exc:  # noqa: BLE001 -- load-test harness: report and move on
        result.setup_error = str(exc)
        return result

    ws_url = f"{gateway_ws_url}/ws/exam/{session_id}?token={token}"
    deadline = time.monotonic() + duration_s

    try:
        async with websockets.connect(ws_url, max_size=None) as ws:
            await asyncio.wait_for(_expect_json_type(ws, "connected"), timeout=10)
            await _drain_initial_burst(ws)

            while time.monotonic() < deadline:
                turn_result = await _run_one_turn(
                    ws, turn_min_s=turn_min_s, turn_max_s=turn_max_s, per_turn_timeout_s=10.0
                )
                result.turns.append(turn_result)
                await asyncio.sleep(random.uniform(0.1, 0.5))  # think time between turns
    except (websockets.exceptions.ConnectionClosed, OSError, asyncio.TimeoutError):
        result.connection_dropped = True

    return result


async def run_load_test(
    *,
    gateway_http_url: str = "http://localhost:8000",
    gateway_ws_url: str = "ws://localhost:8000",
    concurrency: int = 20,
    duration_s: float = 30.0,
    turn_min_s: float = 1.0,
    turn_max_s: float = 3.0,
) -> LoadTestReport:
    sessions = await asyncio.gather(
        *[
            _run_one_session(
                gateway_http_url=gateway_http_url,
                gateway_ws_url=gateway_ws_url,
                duration_s=duration_s,
                turn_min_s=turn_min_s,
                turn_max_s=turn_max_s,
                candidate_index=i,
            )
            for i in range(concurrency)
        ]
    )

    latencies_ms: list[float] = []
    error_count = 0
    total_turns = 0
    for session in sessions:
        for turn in session.turns:
            total_turns += 1
            if turn.latency_ms is not None:
                latencies_ms.append(turn.latency_ms)
            else:
                error_count += 1

    return LoadTestReport(
        concurrency=concurrency,
        duration_s=duration_s,
        total_turns=total_turns,
        error_count=error_count,
        dropped_connections=sum(1 for s in sessions if s.connection_dropped),
        setup_errors=sum(1 for s in sessions if s.setup_error),
        latencies_ms=latencies_ms,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway-http-url", default="http://localhost:8000")
    parser.add_argument("--gateway-ws-url", default="ws://localhost:8000")
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--duration", type=float, default=30.0, help="seconds")
    parser.add_argument("--turn-min-s", type=float, default=1.0)
    parser.add_argument("--turn-max-s", type=float, default=3.0)
    parser.add_argument("--json-out", default=None, help="path to write the report as JSON")
    parser.add_argument(
        "--real-gemini",
        action="store_true",
        help="No effect here -- see module docstring; it's a reminder flag "
        "for whoever launched the target gateway wired to the real vendor.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    report = asyncio.run(
        run_load_test(
            gateway_http_url=args.gateway_http_url,
            gateway_ws_url=args.gateway_ws_url,
            concurrency=args.concurrency,
            duration_s=args.duration,
            turn_min_s=args.turn_min_s,
            turn_max_s=args.turn_max_s,
        )
    )
    print(report.summary())
    if args.json_out:
        with open(args.json_out, "w") as f:
            json.dump(report.to_dict(), f, indent=2)


if __name__ == "__main__":
    main()
