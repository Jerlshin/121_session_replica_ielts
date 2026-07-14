# `packages/shared-schemas`

**Current status: scaffolded, not populated.** This package was reserved
in the initial monorepo layout (`CLAUDE.md`'s tree comment: "Pydantic
models (Python) + Compiled TypeScript types") as the intended home for
schemas shared across `apps/api-gateway`, `apps/worker`, and `apps/web` —
e.g. `FeatureVector`, WebSocket message envelopes, the `JudgeInput`/
`JudgeOutput` judge contract. As of the end of Phase 9, `python/` and
`typescript/` contain only a `.gitkeep` each. This README documents that
gap honestly, explains why it happened, and what would need to change to
actually populate it, rather than describing aspirational content that
doesn't exist in the codebase.

## What actually happened instead

Every cross-app payload shape that this package was meant to centralize
ended up defined **once per consumer**, independently:

| Shape | Where it actually lives | Why |
|---|---|---|
| WebSocket message envelopes (`cue_card`, `timer_deadline`, `turn_complete`, ...) | Python side: implicit dict literals in `apps/api-gateway/app/routers/ws_exam.py`/`exam_orchestrator.py`. TypeScript side: `ServerMessage` union in `apps/web/src/ws/exam-socket-client.ts` | Hand-kept in sync by convention, not by a shared codegen step |
| `FeatureVector` shape (per-criterion metric dicts) | Independently defined inline in each of `apps/worker/tasks/nlp/fluency.py`/`lexical.py`/`grammar.py`/`tasks/pronunciation.py` (each returns its own dict shape) and read back generically as `dict` in `apps/worker/providers/scoring_llm.py`'s `PhaseEvidence` | The feature dicts are intentionally schema-loose per Spec 03 §4 ("self-contained JSON payload") — no single Pydantic model was ever a natural fit across all four criteria's very different metric sets |
| `JudgeInput`/`JudgeOutput`/`CriterionScore` (the LLM judge contract) | `apps/worker/providers/scoring_llm.py` | Only ever consumed by `apps/worker` itself (`tasks/scoring.py`, `calibration_report.py`) — there was no second consumer to share it with |
| SQLAlchemy ORM models | `apps/api-gateway/app/models/` (canonical, async) **and** a separately hand-maintained sync mirror in `apps/worker/models.py` | See [`apps/worker/README.md`](../../apps/worker/README.md#shared-schema-ownership) for the full rationale — Celery tasks run outside an asyncio event loop and cannot share the gateway's async engine, so a single shared ORM package would need a driver-agnostic model layer that was never built |

In every case, the two (or three) independent definitions are kept
consistent **by convention and by integration tests that exercise the
real wire format end-to-end** (e.g.
`tests/integration/test_full_exam_session_flow.py` drives the actual
`/ws/exam/{id}` route and asserts on real JSON payloads), not by a shared
type source.

## Why this is a real gap, not a non-issue

This is the honest tradeoff of the "grow it incrementally as each phase
introduces new cross-app payloads" plan that never actually triggered:
each phase's cross-app payload turned out small enough, and delivered
under enough time pressure, that duplicating a shape once was cheaper in
the moment than building the type-generation pipeline this package
implies (Pydantic models on the Python side, some compiled/generated
TypeScript types on the `apps/web` side, plus a build step to keep them
in sync). The result is a **real drift risk** — nothing prevents
`ws_exam.py`'s server-side message shape and `exam-socket-client.ts`'s
`ServerMessage` union from silently diverging on a future change, and the
only thing that would currently catch it is an integration test noticing
a shape mismatch at runtime.

## What populating this properly would require

1. **Python side (`python/`)**: extract the WS message envelope shapes
   (currently implicit dict literals) into real Pydantic models, and have
   `apps/api-gateway` construct/serialize outgoing WS messages through
   them instead of raw dicts.
2. **TypeScript side (`typescript/`)**: either hand-author matching
   interfaces (todays's status quo, just relocated) or add a real codegen
   step (e.g. `pydantic` → JSON Schema → TypeScript) that runs in CI and
   fails the build on drift.
3. **A decision on the ORM duplication**: unifying `apps/api-gateway`'s
   async models and `apps/worker`'s sync mirror behind one schema source
   would need a driver-agnostic model definition (e.g. plain dataclasses
   or Pydantic models that both an async and sync SQLAlchemy `Base`
   independently map to) — a bigger structural change than this package's
   original scope implied, not undertaken in Phases 0–9.

None of this is scheduled against any phase in
[`docs/SPEC_04_REPOSITORY_AND_BUILD_PLAN.md`](../../docs/SPEC_04_REPOSITORY_AND_BUILD_PLAN.md) —
it is flagged here as known technical debt for whoever picks up
cross-app schema consolidation next, not as work silently deferred without
a trace.

## Cross-references

- The gateway's canonical, authoritative schema definitions: [`apps/api-gateway/README.md`](../../apps/api-gateway/README.md#appmodels--sqlalchemy-schema-async-side)
- The worker's independently-maintained sync mirror and the rationale for it: [`apps/worker/README.md`](../../apps/worker/README.md#shared-schema-ownership)
- The client-side WS message union this package would formalize: [`apps/web/README.md`](../../apps/web/README.md#websocket-client-srcwsexam-socket-clientts)
