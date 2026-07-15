"""Per-connection phase driver: the piece of Phase 3 that turns the pure
`exam-fsm` transition table plus `fsm_engine`'s event-sourced I/O into an
actual running exam (Spec 02, Spec 04 §2). One instance is owned by exactly
one `/ws/exam/{session_id}` connection, mirroring how `ws_exam.py` already
keeps per-connection mutable state in closures — this class is that state,
pulled out because there's now enough of it to need a home.

It is the single place that calls `fsm_engine.transition` (via `_advance`),
which is what keeps its in-memory phase cache and the durable event log from
ever disagreeing about who's driving a transition — timers.py's watchdogs
only detect expiry and report back here.
"""
import asyncio
import logging
import time
import uuid

from exam_fsm import ExamEvent, ExamPhase
from fastapi.concurrency import run_in_threadpool

from app.config import settings
from app.db import AsyncSessionLocal
from app.models import Candidate, CueCard, ExamSession, SessionStatus, TopicSet
from app.services import exam_content, fsm_engine, timers
from app.services.gemini_bridge import GeminiLiveBridge, load_directive
from app.services.grading_trigger import enqueue_grading

logger = logging.getLogger("app.exam_orchestrator")

# Soft phase exits (Spec 02 §1) approximated by a fixed completed-*exchange*
# budget rather than real conversational-arc detection — see Spec 04 build
# notes / CLAUDE.md. An "exchange" is counted on the candidate's own
# activity_end (Gemini asking a question doesn't count — only the candidate
# actually answering does), which is why this is keyed off
# `on_activity_end`, not `on_gemini_turn_complete`. Read from `settings` at
# call time (not module import time) so tests can override the budgets
# before opening a connection.
def _candidate_turn_budget_for_phase(phase: ExamPhase) -> tuple[ExamEvent, int] | None:
    return {
        ExamPhase.INTRO: (ExamEvent.INTRO_COMPLETE, settings.intro_turns),
        ExamPhase.PART1_TOPIC_A: (ExamEvent.TOPIC_A_COMPLETE, settings.part1_topic_turns),
        ExamPhase.PART1_TOPIC_B: (ExamEvent.TOPIC_B_COMPLETE, settings.part1_topic_turns),
        ExamPhase.PART1_TOPIC_C: (ExamEvent.TOPIC_C_COMPLETE, settings.part1_topic_turns),
        ExamPhase.PART2_ROUNDOFF: (ExamEvent.ROUNDOFF_COMPLETE, settings.part2_roundoff_turns),
        ExamPhase.PART3_DISCUSSION: (
            ExamEvent.DISCUSSION_COMPLETE,
            settings.part3_discussion_turns,
        ),
    }.get(phase)


# Part 1's real-exam "4-5 minutes combined" window (Spec 02 §1/§4) spans all
# three topic sub-phases, not any single one — the ceiling watchdog below
# must keep watching across a natural A->B->C rotation, and its forced-skip
# path (when it fires early, e.g. still in A) needs to know which FSM event
# closes out whichever sub-phase is currently active.
PART1_PHASES = frozenset(
    {ExamPhase.PART1_TOPIC_A, ExamPhase.PART1_TOPIC_B, ExamPhase.PART1_TOPIC_C}
)
_PART1_COMPLETION_EVENT: dict[ExamPhase, ExamEvent] = {
    ExamPhase.PART1_TOPIC_A: ExamEvent.TOPIC_A_COMPLETE,
    ExamPhase.PART1_TOPIC_B: ExamEvent.TOPIC_B_COMPLETE,
    ExamPhase.PART1_TOPIC_C: ExamEvent.TOPIC_C_COMPLETE,
}


class ExamOrchestrator:
    def __init__(
        self, *, session_id: uuid.UUID, bridge: GeminiLiveBridge, outbox: asyncio.Queue
    ) -> None:
        self.session_id = session_id
        self.bridge = bridge
        self.outbox = outbox

        self._phase = ExamPhase.INIT_DEVICE_CHECK
        self._turns_in_phase = 0
        self._turns_since_reanchor = 0
        # Wall-clock start of Part 1 (Spec 02 §4's 4-5 minute combined
        # window), mirrored into Redis (timers.mark_phase_start) so a
        # reconnect mid-Part-1 can recover it instead of losing it to this
        # fresh instance variable — see start()'s resume branch.
        self._part1_start_ts: float | None = None
        self._topic_sets: dict[str, TopicSet] = {}
        self._cue_card: CueCard | None = None
        self._watchdog_task: asyncio.Task | None = None
        self._finalizing_watchdog_task: asyncio.Task | None = None
        self._pending_flush_turn_ids: set[uuid.UUID] = set()

        # Guards against "continuous questioning": Gemini's Live session
        # generates a new spoken turn for *any* completed input turn it
        # receives, including our injected [EXAMINER_DIRECTIVE] turns — not
        # just the candidate's own activityEnd. Firing a directive
        # immediately after the candidate's activity_end (before Gemini has
        # even replied to what the candidate just said) queues up two
        # completed input turns back-to-back, and the candidate hears the
        # examiner deliver two responses/questions in a row with no chance
        # to answer either. `_gemini_turn_complete` tracks whether Gemini's
        # reply to whatever we last sent it has actually finished (its own
        # TurnComplete event); `_directive_queue` + `_directive_dispatcher_task`
        # serialize every directive behind that, so "one question at a
        # time" (Spec 02 §6.1 rule 2) is enforced at the transport level,
        # not just trusted to the prompt. Starts "set" — nothing is in
        # flight before the first directive (INTRO) is sent.
        self._gemini_turn_complete = asyncio.Event()
        self._gemini_turn_complete.set()
        self._directive_queue: asyncio.Queue[str] = asyncio.Queue()
        self._directive_dispatcher_task: asyncio.Task | None = None

    @property
    def phase(self) -> ExamPhase:
        return self._phase

    # -- connection lifecycle -------------------------------------------------

    async def start(self) -> None:
        self._directive_dispatcher_task = asyncio.create_task(self._dispatch_directives())

        async with AsyncSessionLocal() as db:
            self._phase = await fsm_engine.get_current_phase(db, self.session_id)

        if self._phase == ExamPhase.INIT_DEVICE_CHECK:
            await self._bootstrap_fresh_session()
            return

        # Resuming mid-exam (Spec 01 §5.3): the full reconnect/grace-window
        # policy is Phase 4's job — this only avoids being actively wrong on
        # resume by re-anchoring guardrails (Spec 02 §6.3) instead of
        # replaying the whole intro, and picking a still-live Part 2
        # watchdog back up against its unchanged Redis deadline (no new
        # deadline is set here — that would reset the clock).
        await self._inject_template("reanchor")
        if self._phase in PART1_PHASES:
            self._part1_start_ts = await timers.get_phase_start(self.session_id, "part1_start")
            await self._repush_live_deadline("part1_max")
            self._watchdog_task = asyncio.create_task(self._run_part1_watchdog())
        elif self._phase == ExamPhase.PART2_PREP:
            # A fresh bridge instance defaults to unmuted (Spec 02 §1) — a
            # resume landing mid-prep must re-apply the mute, not just the
            # timer, or a reconnect during prep would let audio through.
            self.bridge.force_mute_input()
            await self._repush_live_deadline("part2_prep")
            self._watchdog_task = asyncio.create_task(self._run_prep_watchdog())
        elif self._phase == ExamPhase.PART2_LONG_TURN:
            await self._repush_live_deadline("part2_long_turn")
            self._watchdog_task = asyncio.create_task(self._run_long_turn_watchdog())
        elif self._phase == ExamPhase.PART3_DISCUSSION:
            await self._repush_live_deadline("part3_discussion")
            self._watchdog_task = asyncio.create_task(self._run_part3_watchdog())

    async def close(self) -> None:
        # Cancel and await (mirrors ws_exam.py's own shutdown sequence) —
        # cancelling without awaiting can leave a task's `async with
        # AsyncSessionLocal()` block torn down by the garbage collector
        # instead of its own clean __aexit__, which is exactly the kind of
        # dangling-connection warning that shows up under back-to-back
        # rapid phase transitions (Part 1/3's watchdogs included).
        tasks = [
            task
            for task in (
                self._watchdog_task,
                self._finalizing_watchdog_task,
                self._directive_dispatcher_task,
            )
            if task is not None and not task.done()
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _bootstrap_fresh_session(self) -> None:
        # No device-check/ID-capture UI exists yet (Spec 04 §2 Phase 3
        # scope) — these fire as real, logged FSM events rather than being
        # skipped, so the pure FSM's full phase table stays meaningful.
        await self._advance(ExamEvent.DEVICE_CHECK_CONFIRMED, reason="auto_pass_no_device_check_ui")
        await self._advance(ExamEvent.ID_VERIFIED, reason="auto_pass_no_id_verification_ui")

    # -- external triggers ------------------------------------------------

    async def on_gemini_turn_complete(self) -> None:
        # Unblocks anything waiting in `_directive_queue` (see
        # `_dispatch_directives`) — Gemini has now actually finished
        # speaking whatever it was about to say, so it's safe to hand it
        # the next out-of-band directive without talking over itself.
        self._gemini_turn_complete.set()

        # Only CLOSE advances on Gemini's own turn-complete — the closing
        # statement is model-initiated with no candidate reply expected.
        # Every other phase advances on the candidate's activity_end (see
        # on_activity_end below), never on Gemini merely finishing asking a
        # question.
        if self._phase == ExamPhase.CLOSE:
            await self._advance(ExamEvent.CLOSE_DELIVERED, reason="close_delivered")

    async def on_activity_end(self) -> None:
        # The candidate's activityEnd always elicits exactly one Gemini
        # response turn — mark one as in flight so any directive this same
        # call ends up queuing (reanchor, part1_extend, or a phase-advance's
        # on-enter directive) waits for it instead of racing it.
        self._gemini_turn_complete.clear()

        if self._phase == ExamPhase.PART2_LONG_TURN:
            await self._advance(ExamEvent.LONG_TURN_ENDED, reason="candidate_released_ptt")
            return

        budget_entry = _candidate_turn_budget_for_phase(self._phase)
        if budget_entry is None:
            return  # this phase advances via a different trigger (timer, ack)

        event, budget = budget_entry
        self._turns_in_phase += 1
        self._turns_since_reanchor += 1

        budget_reached = self._turns_in_phase >= budget
        # Spec 02 §4's "at least 4 minutes" floor for Part 1 is enforced
        # only at Part 1's single exit point (leaving PART1_TOPIC_C) —
        # gating just this transition already bounds the *total* Part 1
        # duration, so A->B and B->C don't need their own floor checks.
        if (
            budget_reached
            and self._phase == ExamPhase.PART1_TOPIC_C
            and self._part1_start_ts is not None
            and time.time() - self._part1_start_ts < settings.part1_min_seconds
        ):
            await self._inject_template("part1_extend")
            self._turns_since_reanchor = 0
            return

        if not budget_reached:
            if self._turns_since_reanchor >= settings.reanchor_every_n_turns:
                await self._inject_template("reanchor")
                self._turns_since_reanchor = 0
            return

        await self._advance(event, reason=f"turn_budget_reached:{self._turns_in_phase}")

    async def on_cue_card_ack(self) -> None:
        if self._phase == ExamPhase.PART2_CUECARD_PRESENT:
            await self._advance(ExamEvent.CUE_CARD_ACKED, reason="cue_card_acked")

    def on_turn_flush_started(self, turn_id: uuid.UUID) -> ExamPhase:
        """Must be called before `on_activity_end()` for the same turn —
        it captures `self._phase` before any advance that turn's own
        activity_end might trigger, which is the only way to know the
        phase a turn was actually spoken in (Phase 6, Spec 03 §4). Calling
        this after `on_activity_end()` would silently return the *new*
        phase for every turn that completes one, i.e. every phase's last
        turn (see migration 0005's docstring for why)."""
        self._pending_flush_turn_ids.add(turn_id)
        return self._phase

    def on_turn_flush_complete(self, turn_id: uuid.UUID) -> None:
        self._pending_flush_turn_ids.discard(turn_id)

    # -- client-facing timer UI (Spec 04 §2 Phase 8) -------------------------

    async def _push_timer_deadline(self, name: str, deadline_epoch_s: float) -> None:
        """Lets the client render an accurate live countdown instead of
        guessing — the deadline itself is still exclusively server-
        authoritative (`timers.py`'s Redis absolute deadline), this only
        tells the client what it is."""
        await self.outbox.put(
            {
                "type": "timer_deadline",
                "name": name,
                "deadline_epoch_ms": round(deadline_epoch_s * 1000),
            }
        )

    async def _repush_live_deadline(self, name: str) -> None:
        """Resume path counterpart to `_push_timer_deadline`: a reconnecting
        client has no idea what the still-running deadline is until told,
        so re-read it from Redis (never resetting it) and push it again."""
        deadline = await timers.get_deadline(self.session_id, name)
        if deadline is not None:
            await self._push_timer_deadline(name, deadline)

    # -- watchdogs ----------------------------------------------------------

    async def _run_part1_watchdog(self) -> None:
        """Spec 02 §4's hard ceiling on Part 1's combined A/B/C window: if
        still anywhere in Part 1 at part1_max_seconds, wrap up gracefully
        and force-advance straight to Part 2 regardless of which topic
        sub-phase is currently active or how many turns it's had."""
        expired = await timers.wait_for_phase_group_deadline(
            self.session_id, "part1_max", PART1_PHASES
        )
        if not expired:
            return
        await timers.clear_deadline(self.session_id, "part1_max")
        await self._queue_directive(load_directive(settings.prompt_templates_dir, "part1_wrap_up"))
        while self._phase in _PART1_COMPLETION_EVENT:
            event = _PART1_COMPLETION_EVENT[self._phase]
            is_final_hop = self._phase == ExamPhase.PART1_TOPIC_C
            await self._advance(event, reason="part1_max_watchdog", announce=is_final_hop)

    async def _run_part3_watchdog(self) -> None:
        """Spec 02 §4's dynamic ceiling on Part 3: the duration was computed
        once at PART3_DISCUSSION entry (whatever's left of the 11-14 minute
        total, clamped to Part 3's own ~4-5 minute band). If the turn
        budget finishes first, this watchdog just observes the phase
        change and exits cleanly (mirrors Part 2's prep/long-turn
        watchdogs); otherwise it force-advances to CLOSE."""
        expired = await timers.wait_for_phase_group_deadline(
            self.session_id, "part3_discussion", frozenset({ExamPhase.PART3_DISCUSSION})
        )
        if not expired:
            return
        await timers.clear_deadline(self.session_id, "part3_discussion")
        await self._queue_directive(load_directive(settings.prompt_templates_dir, "part3_wrap_up"))
        await self._advance(ExamEvent.DISCUSSION_COMPLETE, reason="part3_dynamic_timer_expired")

    async def _run_prep_watchdog(self) -> None:
        expired = await timers.wait_for_prep_expiry(self.session_id)
        if not expired:
            return
        await self.outbox.put({"type": "scripted_audio", "asset": "please_begin_speaking_now.wav"})
        await timers.clear_deadline(self.session_id, "part2_prep")
        await self._advance(ExamEvent.PREP_TIMER_EXPIRED, reason="prep_timer_expired")

    async def _run_long_turn_watchdog(self) -> None:
        hard_cutoff_reached = await timers.wait_for_long_turn_cutoff(
            self.session_id,
            self.bridge,
            warn_at_s=settings.part2_long_turn_warn_at_seconds,
            hard_cutoff_s=settings.part2_long_turn_seconds,
        )
        if not hard_cutoff_reached:
            return
        await timers.clear_deadline(self.session_id, "part2_long_turn")
        # wait_for_long_turn_cutoff() just sent DIRECTIVE_PART2_HARD_STOP
        # straight to the bridge (bypassing `_directive_queue` on purpose —
        # the hard cutoff must interrupt immediately, never wait its turn).
        # That directive still elicits a new spoken turn from Gemini ("Thank
        # you, that's the end of your two minutes."), so mark one as in
        # flight here — otherwise PART2_ROUNDOFF's queued directive would
        # see a stale "clear" completion event and jump in over it.
        self._gemini_turn_complete.clear()
        await self._advance(ExamEvent.LONG_TURN_ENDED, reason="hard_cutoff")

    async def _run_finalizing_watchdog(self) -> None:
        deadline = time.monotonic() + settings.finalizing_watchdog_seconds
        while self._pending_flush_turn_ids and time.monotonic() < deadline:
            await asyncio.sleep(0.25)
        if self._phase != ExamPhase.FINALIZING:
            return
        reason = (
            "media_flush_drained"
            if not self._pending_flush_turn_ids
            else "finalizing_watchdog_timeout"
        )
        await self._advance(ExamEvent.FINALIZE_COMPLETE, reason=reason)

    # -- core transition + on-enter dispatch ---------------------------------

    async def _advance(
        self,
        event: ExamEvent,
        *,
        reason: str,
        extra: dict | None = None,
        announce: bool = True,
    ) -> None:
        """`announce=False` logs the transition (still the durable,
        event-sourced source of truth, CLAUDE.md rule 5) without running its
        on-enter side effects — used only by the Part 1 ceiling watchdog to
        cascade silently through any topic sub-phases it's skipping past,
        so a candidate who overran topic A doesn't get freshly introduced
        to topic B's questions half a second before being cut off again."""
        async with AsyncSessionLocal() as db:
            next_phase = await fsm_engine.transition(
                db, self.session_id, event, reason=reason, extra=extra
            )
        self._phase = next_phase
        self._turns_in_phase = 0
        self._turns_since_reanchor = 0
        if announce:
            await self._on_enter_phase(next_phase)

    async def _on_enter_phase(self, phase: ExamPhase) -> None:
        if phase == ExamPhase.INTRO:
            async with AsyncSessionLocal() as db:
                session = await db.get(ExamSession, self.session_id)
                candidate = await db.get(Candidate, session.candidate_id)
                self._topic_sets = await exam_content.assign_topic_sets(db, candidate)
                session.topic_set_ids = exam_content.topic_set_ids_payload(self._topic_sets)
                candidate_name = candidate.full_name
                await db.commit()
            # Marks the start of the scored exam content (Spec 02 §4's
            # 11-14 minute overall Speaking-section threshold) — Part 3's
            # dynamic remainder budget reads this back.
            await timers.mark_phase_start(
                self.session_id, "exam_start", ttl_seconds=settings.exam_total_max_seconds + 600
            )
            await self._inject_template("intro", candidate_name=candidate_name)

        elif phase in (ExamPhase.PART1_TOPIC_A, ExamPhase.PART1_TOPIC_B, ExamPhase.PART1_TOPIC_C):
            if phase == ExamPhase.PART1_TOPIC_A:
                # Part 1's single entry point (Spec 02 §4) — starts the
                # 4-5 minute combined floor/ceiling window spanning A/B/C.
                self._part1_start_ts = await timers.mark_phase_start(
                    self.session_id, "part1_start", ttl_seconds=settings.part1_max_seconds + 300
                )
                deadline = await timers.set_deadline(
                    self.session_id, "part1_max", settings.part1_max_seconds
                )
                await self._push_timer_deadline("part1_max", deadline)
                self._watchdog_task = asyncio.create_task(self._run_part1_watchdog())

            slot = phase.value[-1]  # "PART1_TOPIC_A" -> "A"
            topic_set = self._topic_sets[slot]
            await self._inject_template(
                "part1_topic",
                topic_title=topic_set.title,
                questions="; ".join(topic_set.questions),
            )

        elif phase == ExamPhase.PART2_CUECARD_PRESENT:
            async with AsyncSessionLocal() as db:
                cue_card = await exam_content.select_cue_card(db)
                session = await db.get(ExamSession, self.session_id)
                session.cue_card_id = cue_card.id
                await db.commit()
            self._cue_card = cue_card
            await self.outbox.put(
                {
                    "type": "cue_card",
                    "cue_card_id": str(cue_card.id),
                    "topic": cue_card.topic,
                    "bullets": cue_card.bullets,
                }
            )
            await self._inject_template(
                "part2_cuecard_present", topic=cue_card.topic, bullets=", ".join(cue_card.bullets)
            )

        elif phase == ExamPhase.PART2_PREP:
            # Spec 02 §1: prep is silent — mute the candidate's mic stream
            # server-side (not merely trusting the thin client's PTT UI,
            # CLAUDE.md rule 1) so nothing recorded during prep can reach
            # Gemini or be scored.
            self.bridge.force_mute_input()
            deadline = await timers.set_deadline(
                self.session_id, "part2_prep", settings.part2_prep_seconds
            )
            await self._push_timer_deadline("part2_prep", deadline)
            await self.outbox.put(
                {"type": "scripted_audio", "asset": "you_have_one_minute_to_prepare.wav"}
            )
            self._watchdog_task = asyncio.create_task(self._run_prep_watchdog())

        elif phase == ExamPhase.PART2_LONG_TURN:
            # Undo PART2_PREP's mute — the candidate is now expected to speak.
            self.bridge.unmute_input()
            deadline = await timers.set_deadline(
                self.session_id, "part2_long_turn", settings.part2_long_turn_seconds
            )
            await self._push_timer_deadline("part2_long_turn", deadline)
            self._watchdog_task = asyncio.create_task(self._run_long_turn_watchdog())

        elif phase == ExamPhase.PART2_ROUNDOFF:
            # Undo the hard-cutoff's force_mute_input (Spec 02 §3.3/§3.4) —
            # without this, a candidate who ran past 120s would have every
            # later turn (round-off, Part 3) silently dropped for the rest
            # of the connection.
            self.bridge.unmute_input()
            await self._inject_template("part2_roundoff")

        elif phase == ExamPhase.PART3_DISCUSSION:
            themes = self._cue_card.linked_part3_themes if self._cue_card else []
            await self._inject_template("part3_discussion", themes="; ".join(themes))

            # Spec 02 §4: Part 3's timing dynamically fills whatever is left
            # of the overall 11-14 minute exam threshold, clamped to Part
            # 3's own ~4-5 minute band so a very fast or very slow Part 1/2
            # still leaves Part 3 a sane, bounded ceiling rather than
            # racing to zero or running unbounded.
            exam_start = await timers.get_phase_start(self.session_id, "exam_start")
            elapsed_before_part3 = time.time() - exam_start if exam_start is not None else 0.0
            remaining_of_total = settings.exam_total_max_seconds - elapsed_before_part3
            target_duration = min(
                max(remaining_of_total, settings.part3_min_seconds),
                settings.part3_max_seconds,
            )
            deadline = await timers.set_deadline(
                self.session_id, "part3_discussion", target_duration
            )
            await self._push_timer_deadline("part3_discussion", deadline)
            self._watchdog_task = asyncio.create_task(self._run_part3_watchdog())

        elif phase == ExamPhase.CLOSE:
            await self._inject_template("close")

        elif phase == ExamPhase.FINALIZING:
            if not self._pending_flush_turn_ids:
                await self._advance(ExamEvent.FINALIZE_COMPLETE, reason="no_pending_media")
            else:
                self._finalizing_watchdog_task = asyncio.create_task(self._run_finalizing_watchdog())

        elif phase == ExamPhase.COMPLETE:
            async with AsyncSessionLocal() as db:
                session = await db.get(ExamSession, self.session_id)
                session.status = SessionStatus.COMPLETED
                await db.commit()
            # Spec 03 §2.1: COMPLETE enqueues the grading pipeline root job.
            # A broker outage here must never break exam completion on the
            # live FSM path — logged and swallowed, not raised.
            try:
                await run_in_threadpool(enqueue_grading, self.session_id)
            except Exception:
                logger.exception(
                    "failed to enqueue grading pipeline session=%s — session is still "
                    "COMPLETED; grading can be retried out-of-band",
                    self.session_id,
                )

    async def _inject_template(self, name: str, **kwargs) -> None:
        template = load_directive(settings.prompt_templates_dir, name)
        text = template.format(**kwargs) if kwargs else template
        await self._queue_directive(text)

    async def _queue_directive(self, text: str) -> None:
        """Hands `text` to `_dispatch_directives` instead of sending it to
        the bridge inline — enqueuing is instant, so callers like
        `_advance()` still update phase state and commit the event log
        synchronously (CLAUDE.md rule 5); only the actual "tell Gemini"
        side effect is deferred to wait its turn."""
        await self._directive_queue.put(text)

    async def _dispatch_directives(self) -> None:
        """The single place that actually calls `bridge.inject_directive`
        for phase-transition/reanchor directives (Spec 02 §6.2) — serialized
        one at a time behind `_gemini_turn_complete` so a directive is never
        sent while Gemini's reply to the previous turn (the candidate's own,
        or a prior directive's) is still in flight. Without this, the Live
        session would receive two completed input turns back-to-back and
        answer both, producing exactly the "examiner fires off continuous
        questions before the candidate can answer" symptom this exists to
        prevent. Runs for the life of the connection; cancelled in close()."""
        while True:
            text = await self._directive_queue.get()
            try:
                await asyncio.wait_for(
                    self._gemini_turn_complete.wait(),
                    timeout=settings.directive_dispatch_timeout_seconds,
                )
            except TimeoutError:
                # A candidate barge-in (Interrupted) can abort a Gemini turn
                # without it ever emitting TurnComplete — waiting forever
                # here would stall the whole exam behind a signal that's
                # never coming. Log and send anyway rather than hang.
                logger.warning(
                    "session=%s directive dispatch timed out waiting for Gemini "
                    "turn-complete after %.1fs — sending anyway",
                    self.session_id,
                    settings.directive_dispatch_timeout_seconds,
                )
            # Sending this directive itself elicits a new spoken turn from
            # Gemini — re-arm the gate so the *next* queued directive (if
            # any) waits for this one instead of piling on top of it.
            self._gemini_turn_complete.clear()
            await self.bridge.inject_directive(text)
