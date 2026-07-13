"""Exhaustive coverage of every transition in Spec 02 §1 — SPEC_04 §3's
whole reason for `exam-fsm` being pure and separately tested is that "if a
transition test fails, it is a logic bug, full stop." That guarantee only
holds if every (phase, event) pair is checked, not just the happy path.
"""
import itertools

import pytest

from exam_fsm import (
    INITIAL_PHASE,
    TERMINAL_PHASES,
    TRANSITIONS,
    ExamEvent,
    ExamPhase,
    InvalidTransitionError,
    fold,
    transition,
)

# The exact ordered walk from Spec 02 §1/§2: (event, expected_next_phase).
HAPPY_PATH: list[tuple[ExamEvent, ExamPhase]] = [
    (ExamEvent.DEVICE_CHECK_CONFIRMED, ExamPhase.ID_VERIFICATION),
    (ExamEvent.ID_VERIFIED, ExamPhase.INTRO),
    (ExamEvent.INTRO_COMPLETE, ExamPhase.PART1_TOPIC_A),
    (ExamEvent.TOPIC_A_COMPLETE, ExamPhase.PART1_TOPIC_B),
    (ExamEvent.TOPIC_B_COMPLETE, ExamPhase.PART1_TOPIC_C),
    (ExamEvent.TOPIC_C_COMPLETE, ExamPhase.PART2_CUECARD_PRESENT),
    (ExamEvent.CUE_CARD_ACKED, ExamPhase.PART2_PREP),
    (ExamEvent.PREP_TIMER_EXPIRED, ExamPhase.PART2_LONG_TURN),
    (ExamEvent.LONG_TURN_ENDED, ExamPhase.PART2_ROUNDOFF),
    (ExamEvent.ROUNDOFF_COMPLETE, ExamPhase.PART3_DISCUSSION),
    (ExamEvent.DISCUSSION_COMPLETE, ExamPhase.CLOSE),
    (ExamEvent.CLOSE_DELIVERED, ExamPhase.FINALIZING),
    (ExamEvent.FINALIZE_COMPLETE, ExamPhase.COMPLETE),
]


def test_happy_path_walks_every_phase_in_spec_order():
    phase = INITIAL_PHASE
    assert phase == ExamPhase.INIT_DEVICE_CHECK

    for event, expected_next in HAPPY_PATH:
        phase = transition(phase, event)
        assert phase == expected_next

    assert phase == ExamPhase.COMPLETE
    # Every ExamPhase must appear somewhere on the canonical walk — nothing
    # in the phase table is unreachable or forgotten.
    visited = {INITIAL_PHASE, *[p for _, p in HAPPY_PATH]}
    assert visited == set(ExamPhase)


def test_fold_matches_manual_transition_chain():
    events = [event for event, _ in HAPPY_PATH]
    assert fold(events) == ExamPhase.COMPLETE


def test_fold_of_no_events_is_the_initial_phase():
    assert fold([]) == INITIAL_PHASE


@pytest.mark.parametrize(
    ("phase", "event"),
    [
        (ExamPhase.INIT_DEVICE_CHECK, ExamEvent.ID_VERIFIED),
        (ExamPhase.PART2_PREP, ExamEvent.LONG_TURN_ENDED),
        (ExamPhase.PART2_LONG_TURN, ExamEvent.PREP_TIMER_EXPIRED),
        (ExamPhase.INTRO, ExamEvent.TOPIC_A_COMPLETE),
        (ExamPhase.COMPLETE, ExamEvent.FINALIZE_COMPLETE),
        (ExamPhase.COMPLETE, ExamEvent.DEVICE_CHECK_CONFIRMED),
    ],
)
def test_illegal_transitions_raise(phase, event):
    with pytest.raises(InvalidTransitionError):
        transition(phase, event)


def test_exhaustive_negative_and_positive_space():
    """The definitive exhaustiveness check: every one of the |phases| *
    |events| possible pairs is asserted one way or the other — legal pairs
    must return exactly the mapped phase, everything else must raise."""
    all_pairs = set(itertools.product(ExamPhase, ExamEvent))
    legal_pairs = set(TRANSITIONS.keys())
    assert legal_pairs.issubset(all_pairs)

    for phase, event in legal_pairs:
        assert transition(phase, event) == TRANSITIONS[(phase, event)]

    for phase, event in all_pairs - legal_pairs:
        with pytest.raises(InvalidTransitionError):
            transition(phase, event)


def test_no_legal_transition_leaves_a_terminal_phase():
    for (phase, _event), _next_phase in TRANSITIONS.items():
        assert phase not in TERMINAL_PHASES


def test_every_non_terminal_phase_has_at_least_one_legal_exit():
    phases_with_exits = {phase for phase, _event in TRANSITIONS}
    for phase in ExamPhase:
        if phase in TERMINAL_PHASES:
            continue
        assert phase in phases_with_exits, f"{phase} has no legal exit transition"
