"""Event-sourced wrapper around `packages/exam-fsm`'s pure transition logic
(CLAUDE.md rules 4 and 5): this module owns all the I/O — reading/writing
`exam_session_events`, updating `ExamSession.current_phase`'s fast-pointer
snapshot — and never re-implements the transition/fold logic itself. Current
state is always `fold(events)` (Spec 01 §5.2); `ExamSession.current_phase`
is a cache of that, not a second source of truth.
"""
import uuid

from exam_fsm import ExamEvent, ExamPhase, InvalidTransitionError
from exam_fsm import fold as exam_fsm_fold
from exam_fsm import transition as exam_fsm_transition
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ExamSession, ExamSessionEvent

__all__ = ["get_current_phase", "transition", "InvalidTransitionError"]

PHASE_TRANSITION_EVENT_TYPE = "PHASE_TRANSITION"


async def _load_phase_events(db: AsyncSession, session_id: uuid.UUID) -> list[ExamSessionEvent]:
    rows = await db.scalars(
        select(ExamSessionEvent)
        .where(ExamSessionEvent.session_id == session_id)
        .order_by(ExamSessionEvent.seq)
    )
    return list(rows.all())


def _fold_phase(events: list[ExamSessionEvent]) -> ExamPhase:
    phase_events = [
        ExamEvent(event.payload["event"])
        for event in events
        if event.event_type == PHASE_TRANSITION_EVENT_TYPE
    ]
    return exam_fsm_fold(phase_events)


async def get_current_phase(db: AsyncSession, session_id: uuid.UUID) -> ExamPhase:
    events = await _load_phase_events(db, session_id)
    return _fold_phase(events)


async def transition(
    db: AsyncSession,
    session_id: uuid.UUID,
    event: ExamEvent,
    *,
    reason: str,
    extra: dict | None = None,
) -> ExamPhase:
    """Appends one PHASE_TRANSITION event at the next seq and advances the
    `ExamSession.current_phase` snapshot to match. Raises
    `InvalidTransitionError` (from the pure package, uncaught here) if
    `event` isn't legal for the session's current phase — that's always a
    caller bug, not a condition to recover from silently."""
    events = await _load_phase_events(db, session_id)
    current_phase = _fold_phase(events)
    next_phase = exam_fsm_transition(current_phase, event)

    next_seq = (events[-1].seq if events else 0) + 1
    db.add(
        ExamSessionEvent(
            session_id=session_id,
            seq=next_seq,
            event_type=PHASE_TRANSITION_EVENT_TYPE,
            payload={
                "event": event.value,
                "from": current_phase.value,
                "to": next_phase.value,
                "reason": reason,
                **(extra or {}),
            },
        )
    )

    session = await db.get(ExamSession, session_id)
    if session is not None:
        session.current_phase = next_phase.value

    await db.commit()
    return next_phase
