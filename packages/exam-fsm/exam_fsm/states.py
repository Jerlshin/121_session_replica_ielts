from enum import Enum


class ExamPhase(str, Enum):
    """The 14 phases of Spec 02 §1's phase table, in exam order. This is the
    exam-business-logic FSM only — the DISCONNECTED/ABORTED connection-
    resiliency overlay (Spec 02 §2) is orthogonal to it and lives in
    Phase 4, not here."""

    INIT_DEVICE_CHECK = "INIT_DEVICE_CHECK"
    ID_VERIFICATION = "ID_VERIFICATION"
    INTRO = "INTRO"
    PART1_TOPIC_A = "PART1_TOPIC_A"
    PART1_TOPIC_B = "PART1_TOPIC_B"
    PART1_TOPIC_C = "PART1_TOPIC_C"
    PART2_CUECARD_PRESENT = "PART2_CUECARD_PRESENT"
    PART2_PREP = "PART2_PREP"
    PART2_LONG_TURN = "PART2_LONG_TURN"
    PART2_ROUNDOFF = "PART2_ROUNDOFF"
    PART3_DISCUSSION = "PART3_DISCUSSION"
    CLOSE = "CLOSE"
    FINALIZING = "FINALIZING"
    COMPLETE = "COMPLETE"


INITIAL_PHASE = ExamPhase.INIT_DEVICE_CHECK
TERMINAL_PHASES = frozenset({ExamPhase.COMPLETE})

# Spec 02 §3.3 — hard, non-negotiable Part 2 deadlines. Defaults only; the
# gateway's Settings own the actual configured values (and tests override
# them to sub-second durations so CI never waits on a real 60s/120s clock).
DEFAULT_PART2_PREP_SECONDS = 60
DEFAULT_PART2_LONG_TURN_SECONDS = 120
DEFAULT_PART2_LONG_TURN_WARN_AT_SECONDS = 115
