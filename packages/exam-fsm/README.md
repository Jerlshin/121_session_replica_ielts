# `packages/exam-fsm`

The pure, deterministic IELTS exam state machine — the single authoritative
encoding of the phase diagram in
[`docs/SPEC_02_IELTS_FLOW_STATE_MACHINE.md`](../../docs/SPEC_02_IELTS_FLOW_STATE_MACHINE.md)
§1–§2. This package contains **zero** network requests, disk I/O, database
access, or async hooks (CLAUDE.md rule 4) — it is a closed, in-memory
computation over enums and a lookup table, which is what makes it
exhaustively unit-testable and lets a failing test mean, unambiguously,
"the transition logic is wrong," never "was it the network or the state
machine" (SPEC_04 §3's stated reason this package exists as a standalone,
pure unit in the first place).

Every other component that needs to know "what phase is this exam in, and
what is legal next" goes through this package's two functions
(`transition`, `fold`) rather than re-implementing any part of the phase
diagram itself.

## Why this is a separate package, not inline in `apps/api-gateway`

Three enforced properties, all structural rather than conventions to
remember:

1. **Purity is testable in isolation.** Because nothing here touches I/O,
   `packages/exam-fsm/tests/test_transitions.py` can exhaustively assert
   every legal `(phase, event)` pair and every illegal one, with no
   database, no event loop, no mocking — a `KeyError`-shaped bug in the
   transition table is caught by a millisecond-fast test, not discovered
   in an integration test three layers away.
2. **`apps/api-gateway` cannot accidentally special-case a transition.**
   `app/services/fsm_engine.py` is architecturally the *only* place in the
   gateway allowed to call `transition()`/`fold()` — it owns all I/O
   against `exam_session_events`, but never re-derives or overrides what
   phase comes next. If a bug ever caused the gateway's in-memory phase
   cache and the durable event log to disagree, the cause would be in
   `fsm_engine.py`'s I/O bookkeeping, never in this package's transition
   table.
3. **The event-sourced resiliency model (CLAUDE.md rule 5, Spec 01 §5.2)
   depends on replay being pure.** `fold()` is called every time a
   session's current phase needs to be known — on every `activity_end`,
   on every reconnect — by replaying the full ordered event list from
   `INITIAL_PHASE`. If that replay had side effects or depended on wall-
   clock time, replaying it twice could produce different answers;
   because it's pure, `fold(events)` is guaranteed to be the exact same
   phase no matter how many times or where it's computed.

## Module reference

```
exam_fsm/
├── __init__.py       Public API surface (re-exports below)
├── states.py          ExamPhase enum, INITIAL_PHASE, TERMINAL_PHASES, Part 2 timer defaults
└── transitions.py     ExamEvent enum, TRANSITIONS table, transition(), fold(), InvalidTransitionError
```

### `states.py`

- **`ExamPhase`** — the 14 phases of Spec 02 §1's phase table, in exam
  order:

  | Phase | Spec 02 role |
  |---|---|
  | `INIT_DEVICE_CHECK` | Camera/mic connectivity check |
  | `ID_VERIFICATION` | Identity confirmation; video recording begins here |
  | `INTRO` | Examiner introduction |
  | `PART1_TOPIC_A` / `_B` / `_C` | Three rotating Part 1 topic-set slots |
  | `PART2_CUECARD_PRESENT` | Cue card pushed to the client |
  | `PART2_PREP` | Hard 60s silent preparation |
  | `PART2_LONG_TURN` | Hard 120s uninterrupted long turn |
  | `PART2_ROUNDOFF` | One or two brief follow-up questions |
  | `PART3_DISCUSSION` | Abstract discussion thematically linked to the Part 2 cue card |
  | `CLOSE` | Examiner's closing statement |
  | `FINALIZING` | Awaiting in-flight media flushes to drain |
  | `COMPLETE` | Terminal — enqueues the grading pipeline |

  Deliberately **excludes** the `DISCONNECTED`/`ABORTED` connection-
  resiliency overlay from Spec 02 §2 — that overlay is orthogonal to the
  exam-business-logic FSM and lives entirely in `apps/api-gateway`'s
  resiliency handling (Phase 4), not in this package.
- **`INITIAL_PHASE`** = `ExamPhase.INIT_DEVICE_CHECK`; **`TERMINAL_PHASES`**
  = `frozenset({ExamPhase.COMPLETE})`.
- **`DEFAULT_PART2_PREP_SECONDS`** (`60`), **`DEFAULT_PART2_LONG_TURN_SECONDS`**
  (`120`), **`DEFAULT_PART2_LONG_TURN_WARN_AT_SECONDS`** (`115`) — Spec 02
  §3.3's hard, non-negotiable deadlines. These are **defaults only**; the
  actually-enforced values live in `apps/api-gateway`'s `Settings`
  (`part2_prep_seconds` etc.), which tests override to sub-second
  durations so CI never waits on a real 60s/120s clock.

### `transitions.py`

- **`ExamEvent`** — the 13 exit triggers from Spec 02 §1's phase table,
  one per legal edge in the FSM diagram: `DEVICE_CHECK_CONFIRMED`,
  `ID_VERIFIED`, `INTRO_COMPLETE`, `TOPIC_A_COMPLETE`, `TOPIC_B_COMPLETE`,
  `TOPIC_C_COMPLETE`, `CUE_CARD_ACKED`, `PREP_TIMER_EXPIRED`,
  `LONG_TURN_ENDED`, `ROUNDOFF_COMPLETE`, `DISCUSSION_COMPLETE`,
  `CLOSE_DELIVERED`, `FINALIZE_COMPLETE`.
- **`TRANSITIONS: dict[tuple[ExamPhase, ExamEvent], ExamPhase]`** — a flat
  lookup table, deliberately not a class hierarchy or graph library. The
  entire point is that it is trivially exhaustive to review and to unit
  test: every legal edge is one dict entry, and "is this pair legal" is
  one dict lookup.
- **`transition(current: ExamPhase, event: ExamEvent) -> ExamPhase`** —
  looks up `TRANSITIONS[(current, event)]`; raises `InvalidTransitionError`
  for any pair not on the diagram. This is always a real caller bug (the
  FSM being driven out of order), never a condition to swallow, clamp, or
  silently ignore.
- **`fold(events: list[ExamEvent]) -> ExamPhase`** — replays an ordered
  event list from `INITIAL_PHASE`, calling `transition()` once per event.
  This is the entire pure logic behind the event-sourced resiliency model:
  "current state" is never stored as mutable truth, it is always
  *recomputed* from the log.
- **`InvalidTransitionError(ValueError)`** — carries `.phase`/`.event` for
  precise diagnostics; never caught and handled inside this package.

### Public API (`__init__.py`)

```python
from exam_fsm import (
    ExamPhase, ExamEvent, TRANSITIONS,
    INITIAL_PHASE, TERMINAL_PHASES,
    InvalidTransitionError, transition, fold,
    DEFAULT_PART2_PREP_SECONDS,
    DEFAULT_PART2_LONG_TURN_SECONDS,
    DEFAULT_PART2_LONG_TURN_WARN_AT_SECONDS,
)
```

## Explicitly out of scope

- **Connection resiliency** (`DISCONNECTED`/`ABORTED`, disconnect grace
  windows, timer pause-on-disconnect) — Spec 02 §2's overlay, owned by
  `apps/api-gateway` (Phase 4), orthogonal to this pure FSM.
- **Content selection** — which cue card or topic set is bound to a
  session is I/O (a database read/random choice) and lives in
  `apps/api-gateway/app/services/exam_content.py`, never here.
- **Soft-phase-exit heuristics** — Part 1/3's "soft target" exits (turn-
  count budgets, reanchor cadence) are an `apps/api-gateway` orchestration
  policy layered *on top of* this FSM's hard edges, not encoded in
  `TRANSITIONS` itself.

## Consumers

`apps/api-gateway/app/services/fsm_engine.py` is the sole consumer and the
sole place `transition()`/`fold()` are called — see
[`apps/api-gateway/README.md`](../../apps/api-gateway/README.md#appservices--the-actual-engineering)
for how the I/O wrapper folds `exam_session_events` through this package
and keeps `exam_sessions.current_phase` as a cache, never a second source
of truth.

## Installation

```bash
pip install -e packages/exam-fsm            # zero runtime dependencies
pip install -e "packages/exam-fsm[dev]"     # + pytest, ruff
```

Installed as a sibling editable package by both `apps/api-gateway` (a
runtime dependency) and CI — never published or vendored, and not a PEP
508 dependency of `apps/api-gateway/pyproject.toml` (the same pragmatic
"install as a sibling" pattern documented in `.github/workflows/ci.yml`).

## Testing

```bash
pytest packages/exam-fsm/tests
```

`tests/test_transitions.py` is the whole test surface, and it is
deliberately exhaustive rather than representative:

- **Happy path**: walks the full `INIT_DEVICE_CHECK → COMPLETE` sequence
  in Spec 02's exact order, asserting each `transition()` call and the
  final `fold()` result agree.
- **Every illegal `(phase, event)` pair** — generated via
  `itertools.product(ExamPhase, ExamEvent)` filtered against `TRANSITIONS`'s
  legal keys — asserts `InvalidTransitionError` is raised, not just the
  documented happy-path edges.
- **Terminal-phase and initial-phase invariants** against
  `TERMINAL_PHASES`/`INITIAL_PHASE`.

Ruff-only lint gate (no runtime dependencies to audit):

```bash
ruff check packages/exam-fsm
```

## Cross-references

- Full phase table, Part 2 deep-dive, phase-directive injection this FSM's transitions trigger: `docs/SPEC_02_IELTS_FLOW_STATE_MACHINE.md`
- Event-sourced resiliency model this package's `fold()` implements the pure half of: `docs/SPEC_01_SYSTEM_ARCHITECTURE.md` §5
- The only I/O-bearing consumer: [`apps/api-gateway/README.md`](../../apps/api-gateway/README.md)
