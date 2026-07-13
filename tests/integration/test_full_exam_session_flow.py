"""Phase 3 exit criterion, CI-automatable form (Spec 04 §2): the real exam
FSM literally exits requires a live Gemini API + staging environment, which
CI cannot run — so this is the fixture-driven equivalent, driving the real
`/ws/exam/{session_id}` route through every phase in Spec 02 §1, in order,
against the fake local Gemini Live server (Spec 04 §3). Assertions are made
against the durable `exam_session_events` log rather than websocket message
counts, since the orchestrator's server-side cascades (a candidate's PTT
turn can itself trigger a new directive injection and its own scripted
reply) make exact message-count assertions brittle in a way the event log
isn't.
"""
import asyncio
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "apps" / "api-gateway"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.models import ExamSession, ExamSessionEvent  # noqa: E402
from _fake_gemini_live_server import FakeGeminiLiveServerHandle  # noqa: E402

FIXTURE = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "gemini_live_replay"
    / "full_exam_session.json"
)

# The exact ordered walk every session must take (Spec 02 §1/§2) — mirrors
# packages/exam-fsm/tests/test_transitions.py's HAPPY_PATH, checked here
# end-to-end through the real gateway route instead of the pure package.
EXPECTED_PHASE_SEQUENCE = [
    "ID_VERIFICATION",
    "INTRO",
    "PART1_TOPIC_A",
    "PART1_TOPIC_B",
    "PART1_TOPIC_C",
    "PART2_CUECARD_PRESENT",
    "PART2_PREP",
    "PART2_LONG_TURN",
    "PART2_ROUNDOFF",
    "PART3_DISCUSSION",
    "CLOSE",
    "FINALIZING",
    "COMPLETE",
]


def _pcm16_frame(num_samples: int = 320, amplitude: int = 3000) -> bytes:
    samples = [int(amplitude * ((i % 32) / 32.0 - 0.5)) for i in range(num_samples)]
    return struct.pack(f"<{num_samples}h", *samples)


def _send_ptt_turn(ws, num_frames: int = 2) -> None:
    """Drives one PTT turn without ever calling ws.receive() — Starlette's
    WebSocketTestSession send queue is an unbounded plain queue.Queue
    (verified against the installed starlette source), so the server's
    background writer never blocks on the test reading it. This test
    asserts server-side state via the DB instead of counting messages."""
    ws.send_json({"type": "activity_start"})
    for _ in range(num_frames):
        ws.send_bytes(_pcm16_frame())
    ws.send_json({"type": "activity_end"})


async def _fetch_phase_sequence(session_id: str) -> list[str]:
    # A standalone engine, not the app's shared AsyncSessionLocal — that
    # one is bound to whichever loop TestClient's portal last used (same
    # reasoning as _load_resumption_handle in test_exam_room_gemini_relay.py).
    engine = create_async_engine(settings.database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as db:
            rows = (
                await db.scalars(
                    select(ExamSessionEvent)
                    .where(
                        ExamSessionEvent.session_id == session_id,
                        ExamSessionEvent.event_type == "PHASE_TRANSITION",
                    )
                    .order_by(ExamSessionEvent.seq)
                )
            ).all()
            return [row.payload["to"] for row in rows]
    finally:
        await engine.dispose()


async def _fetch_session_status(session_id: str) -> str | None:
    engine = create_async_engine(settings.database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as db:
            session = await db.get(ExamSession, session_id)
            return session.status if session else None
    finally:
        await engine.dispose()


def _wait_for_phase_count(session_id: str, expected_count: int, *, timeout_s: float) -> list[str]:
    deadline = time.monotonic() + timeout_s
    sequence: list[str] = []
    while time.monotonic() < deadline:
        sequence = asyncio.run(_fetch_phase_sequence(session_id))
        if len(sequence) >= expected_count:
            return sequence
        time.sleep(0.05)
    return sequence


def test_full_exam_session_walks_every_phase_in_order():
    fake = FakeGeminiLiveServerHandle(FIXTURE).start()
    original = {
        "gemini_live_ws_url": settings.gemini_live_ws_url,
        "gemini_api_key": settings.gemini_api_key,
        "part2_prep_seconds": settings.part2_prep_seconds,
        "part2_long_turn_seconds": settings.part2_long_turn_seconds,
        "part2_long_turn_warn_at_seconds": settings.part2_long_turn_warn_at_seconds,
        "intro_turns": settings.intro_turns,
        "part1_topic_turns": settings.part1_topic_turns,
        "part2_roundoff_turns": settings.part2_roundoff_turns,
        "part3_discussion_turns": settings.part3_discussion_turns,
        "finalizing_watchdog_seconds": settings.finalizing_watchdog_seconds,
    }
    settings.gemini_live_ws_url = fake.url
    settings.gemini_api_key = "fixture-unused-key"
    # Sub-second/small budgets so this CI-blocking test never waits on a
    # real 60s/120s clock or a real conversational-arc judgment (Spec 04 §2
    # Phase 3 build notes) — the mechanism under test is phase progression,
    # not production timing.
    settings.part2_prep_seconds = 1
    settings.part2_long_turn_seconds = 3
    settings.part2_long_turn_warn_at_seconds = 2.9
    settings.intro_turns = 1
    settings.part1_topic_turns = 1
    settings.part2_roundoff_turns = 1
    settings.part3_discussion_turns = 1
    settings.finalizing_watchdog_seconds = 5

    try:
        with TestClient(app) as client:
            login_response = client.post(
                "/auth/login",
                json={"email": "full-exam-flow@example.com", "full_name": "Full Flow Tester"},
            )
            token = login_response.json()["access_token"]

            session_response = client.post(
                "/sessions", headers={"Authorization": f"Bearer {token}"}
            )
            session_id = session_response.json()["id"]

            with client.websocket_connect(f"/ws/exam/{session_id}?token={token}") as ws:
                # Bootstrap (Spec 04 §2 Phase 3 scope decision): the gateway
                # auto-fires DEVICE_CHECK_CONFIRMED/ID_VERIFIED and lands on
                # INTRO before any candidate action — no device-check/ID
                # UI exists yet.
                # Every intermediate check compares a *prefix* of whatever
                # the poll observed, not the whole returned list: later
                # phases (especially CLOSE, which cascades into FINALIZING
                # on its own turn-complete with no further client action)
                # can legitimately have already advanced past the count
                # being waited for by the time the poll loop wakes up.
                sequence = _wait_for_phase_count(session_id, 2, timeout_s=5.0)
                assert sequence[:2] == EXPECTED_PHASE_SEQUENCE[:2]

                # One PTT turn each clears INTRO and Part 1's three topics
                # (turn budgets overridden to 1 above).
                for expected_len in (3, 4, 5, 6):
                    _send_ptt_turn(ws)
                    sequence = _wait_for_phase_count(session_id, expected_len, timeout_s=5.0)
                    assert sequence[:expected_len] == EXPECTED_PHASE_SEQUENCE[:expected_len]

                # PART2_CUECARD_PRESENT only advances on an explicit client
                # ack (Spec 02 §1's "Card rendered client-side, ack
                # received" exit condition) — no PTT turn does this.
                ws.send_json({"type": "cue_card_ack"})
                sequence = _wait_for_phase_count(session_id, 7, timeout_s=5.0)
                assert sequence[:7] == EXPECTED_PHASE_SEQUENCE[:7]

                # PART2_PREP's hard cutoff is server-side and timer-driven
                # (Spec 02 §3.3) — no client action advances it.
                sequence = _wait_for_phase_count(session_id, 8, timeout_s=5.0)
                assert sequence[:8] == EXPECTED_PHASE_SEQUENCE[:8]

                # The long turn: released well inside the (overridden,
                # short) hard cutoff — the "candidate finishes early" path,
                # not the forced-mute hard-cutoff path.
                _send_ptt_turn(ws)
                sequence = _wait_for_phase_count(session_id, 9, timeout_s=5.0)
                assert sequence[:9] == EXPECTED_PHASE_SEQUENCE[:9]

                # Round-off and Part 3 each clear on one PTT turn.
                for expected_len in (10, 11):
                    _send_ptt_turn(ws)
                    sequence = _wait_for_phase_count(session_id, expected_len, timeout_s=5.0)
                    assert sequence[:expected_len] == EXPECTED_PHASE_SEQUENCE[:expected_len]

                # CLOSE -> FINALIZING -> COMPLETE all fire server-side with
                # no further candidate action (Spec 02 §1: the closing
                # statement is model-initiated, and finalizing drains
                # in-flight media flushes automatically).
                sequence = _wait_for_phase_count(session_id, 13, timeout_s=10.0)
                assert sequence == EXPECTED_PHASE_SEQUENCE

            status = _wait_for_status_completed(session_id)
            assert status == "COMPLETED"
    finally:
        settings.gemini_live_ws_url = original["gemini_live_ws_url"]
        settings.gemini_api_key = original["gemini_api_key"]
        settings.part2_prep_seconds = original["part2_prep_seconds"]
        settings.part2_long_turn_seconds = original["part2_long_turn_seconds"]
        settings.part2_long_turn_warn_at_seconds = original["part2_long_turn_warn_at_seconds"]
        settings.intro_turns = original["intro_turns"]
        settings.part1_topic_turns = original["part1_topic_turns"]
        settings.part2_roundoff_turns = original["part2_roundoff_turns"]
        settings.part3_discussion_turns = original["part3_discussion_turns"]
        settings.finalizing_watchdog_seconds = original["finalizing_watchdog_seconds"]
        fake.stop()


def _wait_for_status_completed(session_id: str, *, timeout_s: float = 5.0) -> str | None:
    deadline = time.monotonic() + timeout_s
    status = None
    while time.monotonic() < deadline:
        status = asyncio.run(_fetch_session_status(session_id))
        if status == "COMPLETED":
            return status
        time.sleep(0.05)
    return status
