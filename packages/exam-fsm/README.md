# exam-fsm

Pure IELTS exam state machine (Spec 02). No network, disk I/O, or async
hooks — deterministic transitions only, exhaustively unit-tested.

- `exam_fsm.states` — `ExamPhase` (the 14 phases of Spec 02 §1) and the
  default Part 2 timer durations (Spec 02 §3.3).
- `exam_fsm.transitions` — `ExamEvent` (the exit triggers), the
  `TRANSITIONS` lookup table encoding the FSM diagram (Spec 02 §2),
  `transition()` (single-step, raises `InvalidTransitionError` on any
  illegal pair), and `fold()` (replays an event list from `INITIAL_PHASE` —
  the pure logic behind the event-sourced resiliency model in CLAUDE.md
  rule 5).

The connection-resiliency overlay (`DISCONNECTED`/`ABORTED`, Spec 02 §2) is
explicitly out of scope here — it's orthogonal to this FSM and is Phase 4's
responsibility (Spec 04 §2).

Install locally with `pip install -e packages/exam-fsm`; `apps/api-gateway`
depends on it as a sibling editable install (see `app/services/fsm_engine.py`
for the I/O wrapper that reads/writes the event log this package interprets).

Implemented in Phase 3 (Spec 04 §2).
