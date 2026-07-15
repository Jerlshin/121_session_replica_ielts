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
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "apps" / "api-gateway"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "support"))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.models import ExamSession, ExamSessionEvent  # noqa: E402
from fake_gemini_live_server import FakeGeminiLiveServerHandle  # noqa: E402

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


def _count_audio_messages(fake: FakeGeminiLiveServerHandle) -> int:
    """Counts frames that actually reached the fake Gemini server — the
    observable proxy for "was gemini_bridge's force_mute_input() engaged".
    A muted bridge never calls `_ws.send()` for audio at all (Spec 02
    §3.3), so a stuck mute is directly visible here as a flat count."""
    return sum(
        1
        for message in fake.received_messages
        if "realtimeInput" in message and "audio" in message["realtimeInput"]
    )


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
        "part1_min_seconds": settings.part1_min_seconds,
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
    # Part 1's new 4-5 minute floor (Spec 02 §4) would otherwise block this
    # test's single-PTT-per-topic walk from ever leaving PART1_TOPIC_C —
    # disabled here since turn-budget progression, not real timing, is what
    # this test exercises (see test_part1_floor_* below for the floor itself).
    settings.part1_min_seconds = 0
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
        settings.part1_min_seconds = original["part1_min_seconds"]
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


@contextmanager
def _override_settings(**overrides):
    """Snapshots and restores just the settings a given test touches —
    the same override/restore discipline as the big end-to-end test above,
    factored out so each timing-specific test below only has to name the
    handful of knobs it actually cares about."""
    original = {key: getattr(settings, key) for key in overrides}
    for key, value in overrides.items():
        setattr(settings, key, value)
    try:
        yield
    finally:
        for key, value in original.items():
            setattr(settings, key, value)


def _login_and_create_session(client: TestClient, email: str) -> tuple[str, str]:
    login_response = client.post(
        "/auth/login", json={"email": email, "full_name": "Timing Test Candidate"}
    )
    token = login_response.json()["access_token"]
    session_response = client.post("/sessions", headers={"Authorization": f"Bearer {token}"})
    return token, session_response.json()["id"]


def test_part1_ceiling_watchdog_forces_advance_to_part2_without_turn_budget():
    """Spec 02 §4's "no more than 5 minutes" ceiling: even if the candidate
    never gives enough turns to satisfy the per-topic budget, Part 1 must
    still hand off to Part 2 once part1_max_seconds elapses — from any of
    its three topic sub-phases, not just the last one."""
    fake = FakeGeminiLiveServerHandle(FIXTURE).start()
    try:
        with _override_settings(
            gemini_live_ws_url=fake.url,
            gemini_api_key="fixture-unused-key",
            intro_turns=1,
            part1_topic_turns=1000,  # never reached by turn budget alone
            part1_min_seconds=0,
            part1_max_seconds=1.0,  # hard ceiling fires almost immediately
        ):
            with TestClient(app) as client:
                token, session_id = _login_and_create_session(
                    client, "part1-ceiling@example.com"
                )
                with client.websocket_connect(f"/ws/exam/{session_id}?token={token}") as ws:
                    sequence = _wait_for_phase_count(session_id, 2, timeout_s=5.0)
                    assert sequence[:2] == EXPECTED_PHASE_SEQUENCE[:2]

                    _send_ptt_turn(ws)  # clears INTRO -> PART1_TOPIC_A
                    sequence = _wait_for_phase_count(session_id, 3, timeout_s=5.0)
                    assert sequence[:3] == EXPECTED_PHASE_SEQUENCE[:3]

                    # No further PTT turns: PART1_TOPIC_A's own budget
                    # (1000) is nowhere close to being reached, so only the
                    # ceiling watchdog can advance this. It should cascade
                    # silently through B and C straight to PART2_CUECARD_PRESENT.
                    sequence = _wait_for_phase_count(session_id, 6, timeout_s=5.0)
                    assert sequence[:6] == EXPECTED_PHASE_SEQUENCE[:6]
    finally:
        fake.stop()


def test_part1_floor_blocks_early_exit_until_min_elapsed():
    """Spec 02 §4's "at least 4 minutes" floor: even once the last topic's
    turn budget is satisfied, Part 1 must not hand off to Part 2 before
    part1_min_seconds has actually elapsed since PART1_TOPIC_A began."""
    fake = FakeGeminiLiveServerHandle(FIXTURE).start()
    try:
        with _override_settings(
            gemini_live_ws_url=fake.url,
            gemini_api_key="fixture-unused-key",
            intro_turns=1,
            part1_topic_turns=1,
            part1_min_seconds=1.0,
            part1_max_seconds=30,  # far enough out that the ceiling can't interfere
        ):
            with TestClient(app) as client:
                token, session_id = _login_and_create_session(client, "part1-floor@example.com")
                with client.websocket_connect(f"/ws/exam/{session_id}?token={token}") as ws:
                    _wait_for_phase_count(session_id, 2, timeout_s=5.0)

                    for expected_len in (3, 4, 5):
                        _send_ptt_turn(ws)  # INTRO -> A -> B -> C
                        sequence = _wait_for_phase_count(session_id, expected_len, timeout_s=5.0)
                        assert sequence[:expected_len] == EXPECTED_PHASE_SEQUENCE[:expected_len]

                    # PART1_TOPIC_C's own turn budget (1) is satisfied
                    # immediately, but part1_min_seconds hasn't elapsed yet
                    # — this turn must be absorbed as an extension, not a
                    # transition.
                    _send_ptt_turn(ws)
                    time.sleep(0.4)
                    sequence = asyncio.run(_fetch_phase_sequence(session_id))
                    assert sequence[-1] == "PART1_TOPIC_C", (
                        "Part 1 exited before its 4-minute-equivalent floor elapsed"
                    )

                    # Once the floor has elapsed, the next turn is free to
                    # close out Part 1 normally.
                    time.sleep(0.8)
                    _send_ptt_turn(ws)
                    sequence = _wait_for_phase_count(session_id, 6, timeout_s=5.0)
                    assert sequence[:6] == EXPECTED_PHASE_SEQUENCE[:6]
    finally:
        fake.stop()


def test_part3_ceiling_watchdog_forces_advance_to_close():
    """Spec 02 §4's dynamically-computed Part 3 ceiling: if the turn budget
    never completes naturally, the phase must still hand off to CLOSE once
    the computed remainder-of-total budget elapses."""
    fake = FakeGeminiLiveServerHandle(FIXTURE).start()
    try:
        with _override_settings(
            gemini_live_ws_url=fake.url,
            gemini_api_key="fixture-unused-key",
            part2_prep_seconds=0.3,
            part2_long_turn_seconds=3,
            part2_long_turn_warn_at_seconds=2.9,
            intro_turns=1,
            part1_topic_turns=1,
            part1_min_seconds=0,
            part2_roundoff_turns=1,
            part3_discussion_turns=1000,  # never reached by turn budget alone
            part3_min_seconds=0.5,
            part3_max_seconds=0.5,  # deterministic short ceiling regardless
            # of exam_total_max_seconds/elapsed-time interplay
        ):
            with TestClient(app) as client:
                token, session_id = _login_and_create_session(client, "part3-ceiling@example.com")
                with client.websocket_connect(f"/ws/exam/{session_id}?token={token}") as ws:
                    _wait_for_phase_count(session_id, 2, timeout_s=5.0)

                    for expected_len in (3, 4, 5, 6):
                        _send_ptt_turn(ws)
                        sequence = _wait_for_phase_count(session_id, expected_len, timeout_s=5.0)
                        assert sequence[:expected_len] == EXPECTED_PHASE_SEQUENCE[:expected_len]

                    ws.send_json({"type": "cue_card_ack"})
                    _wait_for_phase_count(session_id, 7, timeout_s=5.0)  # PART2_PREP
                    _wait_for_phase_count(session_id, 8, timeout_s=5.0)  # PART2_LONG_TURN

                    _send_ptt_turn(ws)  # released well inside the long cutoff
                    sequence = _wait_for_phase_count(session_id, 9, timeout_s=5.0)
                    assert sequence[:9] == EXPECTED_PHASE_SEQUENCE[:9]

                    _send_ptt_turn(ws)  # clears PART2_ROUNDOFF -> PART3_DISCUSSION
                    sequence = _wait_for_phase_count(session_id, 10, timeout_s=5.0)
                    assert sequence[:10] == EXPECTED_PHASE_SEQUENCE[:10]

                    # No further PTT turns: part3_discussion_turns (1000) is
                    # nowhere close, so only the dynamic ceiling watchdog
                    # can advance this to CLOSE.
                    sequence = _wait_for_phase_count(session_id, 11, timeout_s=5.0)
                    assert sequence[:11] == EXPECTED_PHASE_SEQUENCE[:11]
    finally:
        fake.stop()


def test_part2_hard_cutoff_unmutes_input_for_subsequent_turns():
    """Regression test for a mute-leak bug: gemini_bridge.force_mute_input()
    is invoked at the Part 2 hard cutoff (Spec 02 §3.3/§3.4), but nothing
    ever called unmute_input() afterward — so a candidate who ran past the
    120s limit would have every later turn (round-off, Part 3) silently
    dropped for the rest of the connection. This drives the candidate
    straight through the hard-cutoff path (holding PTT past the deadline
    instead of releasing early) and asserts audio sent in a later phase
    still reaches the fake Gemini server."""
    fake = FakeGeminiLiveServerHandle(FIXTURE).start()
    try:
        with _override_settings(
            gemini_live_ws_url=fake.url,
            gemini_api_key="fixture-unused-key",
            part2_prep_seconds=0.3,
            part2_long_turn_seconds=0.5,
            part2_long_turn_warn_at_seconds=0.4,
            intro_turns=1,
            part1_topic_turns=1,
            part1_min_seconds=0,
            # Two round-off turns so the stale, over-held turn's eventual
            # activity_end (turn 1) doesn't immediately close out
            # round-off before we can test a deliberate, fresh turn (turn 2).
            part2_roundoff_turns=2,
            part3_discussion_turns=1,
        ):
            with TestClient(app) as client:
                token, session_id = _login_and_create_session(client, "part2-mute@example.com")
                with client.websocket_connect(f"/ws/exam/{session_id}?token={token}") as ws:
                    _wait_for_phase_count(session_id, 2, timeout_s=5.0)

                    for expected_len in (3, 4, 5, 6):
                        _send_ptt_turn(ws)
                        sequence = _wait_for_phase_count(session_id, expected_len, timeout_s=5.0)
                        assert sequence[:expected_len] == EXPECTED_PHASE_SEQUENCE[:expected_len]

                    ws.send_json({"type": "cue_card_ack"})
                    _wait_for_phase_count(session_id, 7, timeout_s=5.0)  # PART2_PREP
                    _wait_for_phase_count(session_id, 8, timeout_s=5.0)  # PART2_LONG_TURN

                    # Hold PTT straight through the hard cutoff instead of
                    # releasing — the server must force-mute and advance on
                    # its own, without waiting for activity_end.
                    ws.send_json({"type": "activity_start"})
                    ws.send_bytes(_pcm16_frame())
                    time.sleep(1.0)  # past the 0.5s deadline + poll interval

                    sequence = _wait_for_phase_count(session_id, 9, timeout_s=5.0)
                    assert sequence[:9] == EXPECTED_PHASE_SEQUENCE[:9]

                    # Release the stale, over-held PTT — consumes
                    # round-off's first turn slot.
                    ws.send_json({"type": "activity_end"})
                    time.sleep(0.3)

                    before = _count_audio_messages(fake)
                    _send_ptt_turn(ws)  # a fresh, clean turn
                    time.sleep(0.3)
                    after = _count_audio_messages(fake)
                    assert after > before, (
                        "candidate audio was dropped after the Part 2 hard cutoff — "
                        "force_mute_input() was never undone"
                    )

                    sequence = _wait_for_phase_count(session_id, 10, timeout_s=5.0)
                    assert sequence[:10] == EXPECTED_PHASE_SEQUENCE[:10]
    finally:
        fake.stop()


def _first_client_content_index_after(messages: list[dict], after_index: int) -> int | None:
    return next(
        (i for i in range(after_index + 1, len(messages)) if "clientContent" in messages[i]),
        None,
    )


def _last_activity_end_index(messages: list[dict]) -> int | None:
    indices = [
        i
        for i, message in enumerate(messages)
        if "realtimeInput" in message and "activityEnd" in message["realtimeInput"]
    ]
    return max(indices) if indices else None


def test_directive_not_sent_until_prior_gemini_turn_completes():
    """Regression test for the "examiner fires off continuous questions
    before the candidate can answer" bug: exam_orchestrator.py used to call
    bridge.inject_directive() for a phase-transition directive immediately
    after the candidate's own activity_end, without waiting for Gemini's
    reply to that same activity_end to actually finish (its TurnComplete).
    Since the Live API generates a new spoken turn for *any* completed
    input turn it receives — including our injected [EXAMINER_DIRECTIVE]
    turns, not just the candidate's own audio — that raced two output turns
    back-to-back with no candidate input in between.

    This configures the fake Gemini server to reply slowly to activityEnd
    and drives the one candidate turn that clears INTRO (intro_turns=1)
    straight into PART1_TOPIC_A, whose on-enter directive is queued
    immediately behind it. If the dispatcher (exam_orchestrator._dispatch_
    directives) is correctly waiting for TurnComplete rather than racing
    it, that directive can't reach the fake server until at least the
    configured delay has elapsed since activity_end arrived.
    """
    delay_s = 1.0
    fake = FakeGeminiLiveServerHandle(FIXTURE, activity_end_delay_s=delay_s).start()
    try:
        with _override_settings(
            gemini_live_ws_url=fake.url,
            gemini_api_key="fixture-unused-key",
            intro_turns=1,
        ):
            with TestClient(app) as client:
                token, session_id = _login_and_create_session(
                    client, "turn-taking@example.com"
                )
                with client.websocket_connect(f"/ws/exam/{session_id}?token={token}") as ws:
                    _wait_for_phase_count(session_id, 2, timeout_s=5.0)

                    _send_ptt_turn(ws)  # clears INTRO -> PART1_TOPIC_A
                    sequence = _wait_for_phase_count(session_id, 3, timeout_s=5.0)
                    assert sequence[:3] == EXPECTED_PHASE_SEQUENCE[:3]

                    # Phase state (the assertion above) advances immediately
                    # on the candidate's activity_end — only the spoken
                    # directive to Gemini is deferred, so give that time to
                    # actually land before inspecting what the fake server saw.
                    deadline = time.monotonic() + delay_s + 5.0
                    directive_index = None
                    activity_end_index = None
                    while time.monotonic() < deadline:
                        messages = fake.received_messages
                        activity_end_index = _last_activity_end_index(messages)
                        if activity_end_index is not None:
                            directive_index = _first_client_content_index_after(
                                messages, activity_end_index
                            )
                            if directive_index is not None:
                                break
                        time.sleep(0.05)

                    assert activity_end_index is not None and directive_index is not None, (
                        "PART1_TOPIC_A's directive never reached the fake Gemini server"
                    )

                    elapsed = (
                        fake.received_at[directive_index] - fake.received_at[activity_end_index]
                    )
                    assert elapsed >= delay_s - 0.1, (
                        f"directive for PART1_TOPIC_A was sent only {elapsed:.2f}s after "
                        f"activity_end (expected >= {delay_s}s) — the dispatcher did not wait "
                        "for Gemini's prior TurnComplete before injecting the next directive, "
                        "reintroducing the continuous-questioning race"
                    )
    finally:
        fake.stop()
