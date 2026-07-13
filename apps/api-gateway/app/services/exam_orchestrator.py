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
        self._topic_sets: dict[str, TopicSet] = {}
        self._cue_card: CueCard | None = None
        self._watchdog_task: asyncio.Task | None = None
        self._finalizing_watchdog_task: asyncio.Task | None = None
        self._pending_flush_turn_ids: set[uuid.UUID] = set()

    @property
    def phase(self) -> ExamPhase:
        return self._phase

    # -- connection lifecycle -------------------------------------------------

    async def start(self) -> None:
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
        if self._phase == ExamPhase.PART2_PREP:
            self._watchdog_task = asyncio.create_task(self._run_prep_watchdog())
        elif self._phase == ExamPhase.PART2_LONG_TURN:
            self._watchdog_task = asyncio.create_task(self._run_long_turn_watchdog())

    async def close(self) -> None:
        for task in (self._watchdog_task, self._finalizing_watchdog_task):
            if task is not None and not task.done():
                task.cancel()

    async def _bootstrap_fresh_session(self) -> None:
        # No device-check/ID-capture UI exists yet (Spec 04 §2 Phase 3
        # scope) — these fire as real, logged FSM events rather than being
        # skipped, so the pure FSM's full phase table stays meaningful.
        await self._advance(ExamEvent.DEVICE_CHECK_CONFIRMED, reason="auto_pass_no_device_check_ui")
        await self._advance(ExamEvent.ID_VERIFIED, reason="auto_pass_no_id_verification_ui")

    # -- external triggers ------------------------------------------------

    async def on_gemini_turn_complete(self) -> None:
        # Only CLOSE advances on Gemini's own turn-complete — the closing
        # statement is model-initiated with no candidate reply expected.
        # Every other phase advances on the candidate's activity_end (see
        # on_activity_end below), never on Gemini merely finishing asking a
        # question.
        if self._phase == ExamPhase.CLOSE:
            await self._advance(ExamEvent.CLOSE_DELIVERED, reason="close_delivered")

    async def on_activity_end(self) -> None:
        if self._phase == ExamPhase.PART2_LONG_TURN:
            await self._advance(ExamEvent.LONG_TURN_ENDED, reason="candidate_released_ptt")
            return

        budget_entry = _candidate_turn_budget_for_phase(self._phase)
        if budget_entry is None:
            return  # this phase advances via a different trigger (timer, ack)

        event, budget = budget_entry
        self._turns_in_phase += 1
        self._turns_since_reanchor += 1

        if self._turns_in_phase < budget:
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

    # -- watchdogs ----------------------------------------------------------

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

    async def _advance(self, event: ExamEvent, *, reason: str, extra: dict | None = None) -> None:
        async with AsyncSessionLocal() as db:
            next_phase = await fsm_engine.transition(
                db, self.session_id, event, reason=reason, extra=extra
            )
        self._phase = next_phase
        self._turns_in_phase = 0
        self._turns_since_reanchor = 0
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
            await self._inject_template("intro", candidate_name=candidate_name)

        elif phase in (ExamPhase.PART1_TOPIC_A, ExamPhase.PART1_TOPIC_B, ExamPhase.PART1_TOPIC_C):
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
            await timers.set_deadline(self.session_id, "part2_prep", settings.part2_prep_seconds)
            await self.outbox.put(
                {"type": "scripted_audio", "asset": "you_have_one_minute_to_prepare.wav"}
            )
            self._watchdog_task = asyncio.create_task(self._run_prep_watchdog())

        elif phase == ExamPhase.PART2_LONG_TURN:
            await timers.set_deadline(
                self.session_id, "part2_long_turn", settings.part2_long_turn_seconds
            )
            self._watchdog_task = asyncio.create_task(self._run_long_turn_watchdog())

        elif phase == ExamPhase.PART2_ROUNDOFF:
            await self._inject_template("part2_roundoff")

        elif phase == ExamPhase.PART3_DISCUSSION:
            themes = self._cue_card.linked_part3_themes if self._cue_card else []
            await self._inject_template("part3_discussion", themes="; ".join(themes))

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
        await self.bridge.inject_directive(text)
