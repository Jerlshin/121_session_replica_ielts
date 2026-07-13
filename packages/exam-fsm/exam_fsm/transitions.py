from enum import Enum

from exam_fsm.states import INITIAL_PHASE, ExamPhase


class ExamEvent(str, Enum):
    """The exit triggers from Spec 02 §1's phase table, one per legal
    transition edge in the FSM diagram (Spec 02 §2)."""

    DEVICE_CHECK_CONFIRMED = "DEVICE_CHECK_CONFIRMED"
    ID_VERIFIED = "ID_VERIFIED"
    INTRO_COMPLETE = "INTRO_COMPLETE"
    TOPIC_A_COMPLETE = "TOPIC_A_COMPLETE"
    TOPIC_B_COMPLETE = "TOPIC_B_COMPLETE"
    TOPIC_C_COMPLETE = "TOPIC_C_COMPLETE"
    CUE_CARD_ACKED = "CUE_CARD_ACKED"
    PREP_TIMER_EXPIRED = "PREP_TIMER_EXPIRED"
    LONG_TURN_ENDED = "LONG_TURN_ENDED"
    ROUNDOFF_COMPLETE = "ROUNDOFF_COMPLETE"
    DISCUSSION_COMPLETE = "DISCUSSION_COMPLETE"
    CLOSE_DELIVERED = "CLOSE_DELIVERED"
    FINALIZE_COMPLETE = "FINALIZE_COMPLETE"


class InvalidTransitionError(ValueError):
    """Raised when `event` is not a legal exit trigger for `phase` — this is
    always a real logic bug (a caller driving the FSM out of order), never a
    condition to swallow or work around (SPEC_04 §3: "if a transition test
    fails, it is a logic bug, full stop")."""

    def __init__(self, phase: ExamPhase, event: ExamEvent) -> None:
        self.phase = phase
        self.event = event
        super().__init__(f"{event.value!r} is not a valid event for phase {phase.value!r}")


# The single linear path through Spec 02's FSM diagram (§2). Deliberately a
# flat lookup table, not a class hierarchy or graph library — the whole
# point (CLAUDE.md rule 4) is that this is trivially exhaustive to review
# and to unit test.
TRANSITIONS: dict[tuple[ExamPhase, ExamEvent], ExamPhase] = {
    (ExamPhase.INIT_DEVICE_CHECK, ExamEvent.DEVICE_CHECK_CONFIRMED): ExamPhase.ID_VERIFICATION,
    (ExamPhase.ID_VERIFICATION, ExamEvent.ID_VERIFIED): ExamPhase.INTRO,
    (ExamPhase.INTRO, ExamEvent.INTRO_COMPLETE): ExamPhase.PART1_TOPIC_A,
    (ExamPhase.PART1_TOPIC_A, ExamEvent.TOPIC_A_COMPLETE): ExamPhase.PART1_TOPIC_B,
    (ExamPhase.PART1_TOPIC_B, ExamEvent.TOPIC_B_COMPLETE): ExamPhase.PART1_TOPIC_C,
    (ExamPhase.PART1_TOPIC_C, ExamEvent.TOPIC_C_COMPLETE): ExamPhase.PART2_CUECARD_PRESENT,
    (ExamPhase.PART2_CUECARD_PRESENT, ExamEvent.CUE_CARD_ACKED): ExamPhase.PART2_PREP,
    (ExamPhase.PART2_PREP, ExamEvent.PREP_TIMER_EXPIRED): ExamPhase.PART2_LONG_TURN,
    (ExamPhase.PART2_LONG_TURN, ExamEvent.LONG_TURN_ENDED): ExamPhase.PART2_ROUNDOFF,
    (ExamPhase.PART2_ROUNDOFF, ExamEvent.ROUNDOFF_COMPLETE): ExamPhase.PART3_DISCUSSION,
    (ExamPhase.PART3_DISCUSSION, ExamEvent.DISCUSSION_COMPLETE): ExamPhase.CLOSE,
    (ExamPhase.CLOSE, ExamEvent.CLOSE_DELIVERED): ExamPhase.FINALIZING,
    (ExamPhase.FINALIZING, ExamEvent.FINALIZE_COMPLETE): ExamPhase.COMPLETE,
}


def transition(current: ExamPhase, event: ExamEvent) -> ExamPhase:
    """Pure, deterministic, exhaustively-tested (CLAUDE.md rule 4) — no
    network, disk I/O, or async hooks. Raises InvalidTransitionError for any
    (phase, event) pair not on the FSM diagram, rather than silently
    ignoring or clamping it."""
    try:
        return TRANSITIONS[(current, event)]
    except KeyError:
        raise InvalidTransitionError(current, event) from None


def fold(events: list[ExamEvent]) -> ExamPhase:
    """Replays an ordered list of exit-trigger events from INITIAL_PHASE —
    the pure logic behind CLAUDE.md rule 5's event-sourced resiliency.
    Callers (`fsm_engine.py`) own reading the event log; this function owns
    the only correct way to interpret it."""
    phase = INITIAL_PHASE
    for event in events:
        phase = transition(phase, event)
    return phase
