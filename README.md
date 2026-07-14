# Virtual IELTS Speaking Examination Platform

An end-to-end, automated IELTS Speaking examination system: a live,
ultra-low-latency, one-to-one spoken conversation loop with a Gemini-
powered virtual Examiner, backed by an asynchronous, evidence-grounded,
four-criteria grading engine and an LLM Rubric Judge. Built in nine
sequential phases against the specifications in [`docs/`](docs), each
phase's exit criteria proven before the next began (Spec 04 §3's
"anti-regression discipline").

## What this system does

A candidate logs in, joins a live WebSocket exam room, and holds a
Push-to-Talk button to speak with a virtual Examiner voiced by Gemini's
Live API — through the standard IELTS Speaking structure (Introduction,
Part 1 topical questions, Part 2 long turn with a cue card and hard 60s
prep / 120s speaking timers, Part 3 abstract discussion, Close). Every
phase transition, timer, and turn boundary is decided **server-side**;
the browser only renders what it's told and forwards raw audio input.
Once the session completes, an asynchronous Celery pipeline stitches and
re-transcribes the canonical audio, computes deterministic linguistic
features for all four IELTS criteria (Fluency & Coherence, Lexical
Resource, Grammatical Range & Accuracy, Pronunciation), and hands that
evidence — never a bare transcript — to an LLM Rubric Judge that scores
against the official band descriptors and cites the specific features it
used. Two independent judge passes are reconciled; a disagreement beyond
a configurable threshold routes the session to mandatory human review
instead of being silently averaged away.

## Non-negotiable design principles

These are enforced structurally throughout the codebase, not just
documented as intent (full detail in [`CLAUDE.md`](CLAUDE.md)):

1. **Server-authoritative control** — the client never decides its own
   phase transitions, timer completions, or evaluations.
2. **Push-to-Talk over VAD** — Gemini's automatic voice-activity
   detection is explicitly disabled; turn boundaries are driven solely by
   the client's `activityStart`/`activityEnd`.
3. **Decoupled video & audio paths** — proctoring video is captured,
   uploaded, and stored entirely independently of the live audio loop and
   is architecturally unreachable from the grading pipeline
   (`apps/worker` never imports the video model at all).
4. **`packages/exam-fsm` stays pure** — no network, disk I/O, or async
   hooks, so a failing FSM test is unambiguously a logic bug.
5. **Event-sourced resiliency** — current session state is always folded
   from an append-only event log, never trusted as mutable "current
   state," so a pod failover or browser reload resumes by querying the
   log.
6. **Evidence before judgment** — the grading engine extracts
   deterministic numeric features first; the LLM Judge is instructed to
   ground every justification in specific pre-computed values, never a
   freeform impression of the raw transcript.
7. **Strict persona adherence** — the Examiner's neutral, non-praising
   character is reinforced via versioned, out-of-band directive injection,
   never inline strings in application code.

## Architecture at a glance

```
┌─────────────┐   WSS + REST    ┌───────────────────┐   raw WS JSON    ┌──────────────┐
│  apps/web    │◄───────────────►│ apps/api-gateway   │◄────────────────►│ Gemini Live  │
│  (Next.js)   │                 │ (FastAPI, stateful │                  │ API          │
└─────────────┘                 │  WS gateway)        │                  └──────────────┘
                                  └────────┬───────────┘
                                           │ enqueue on COMPLETE
                                           ▼
                                  ┌────────────────────┐   Deepgram / Azure /
                                  │  apps/worker         │◄──Claude / (fallback:
                                  │  (Celery grading      │   WhisperX / LanguageTool /
                                  │   engine)             │   self-hosted GOP)
                                  └────────────────────┘
      Shared: Postgres (schema owned by apps/api-gateway) · Redis (session/timer cache)
              · RabbitMQ (Celery broker) · S3/MinIO (raw audio, canonical audio, video)
```

## Repository layout

```
.
├── apps/
│   ├── web/                  Next.js 14 client — README: apps/web/README.md
│   ├── api-gateway/          FastAPI stateful WS gateway — README: apps/api-gateway/README.md
│   └── worker/                Celery async grading engine — README: apps/worker/README.md
├── packages/
│   ├── exam-fsm/               Pure exam state machine — README: packages/exam-fsm/README.md
│   ├── prompt-templates/       Versioned Gemini persona/directives — README: packages/prompt-templates/README.md
│   ├── grading-rubric-assets/  Licensed rubric text injection point — README: packages/grading-rubric-assets/README.md
│   └── shared-schemas/         Reserved, not populated — README explains why: packages/shared-schemas/README.md
├── infra/                     Docker Compose dev stack + Prometheus/Grafana — README: infra/README.md
├── migrations/                 Alembic schema history — README: migrations/README.md
├── tests/                      Integration/load suites, shared fixtures — README: tests/README.md
└── docs/                       The four engineering specifications + one ADR (see below)
```

Every directory listed above has its own comprehensive README covering
that module's architectural role, internal component structure,
configuration knobs, dependencies, and testing protocol — this file is
the map, not a substitute for reading them.

## The specifications (single source of truth)

All implementation is required to strictly align with:

- [`docs/SPEC_01_SYSTEM_ARCHITECTURE.md`](docs/SPEC_01_SYSTEM_ARCHITECTURE.md) — component diagram, tech stack, live pipeline routing & latency budget, state/resiliency management, core data model, object storage topology, security & compliance, non-functional targets.
- [`docs/SPEC_02_IELTS_FLOW_STATE_MACHINE.md`](docs/SPEC_02_IELTS_FLOW_STATE_MACHINE.md) — the full exam phase table, Part 2's hard-timer orchestration deep-dive, and the phase-directive injection mechanism.
- [`docs/SPEC_03_ASYNC_GRADING_ENGINE.md`](docs/SPEC_03_ASYNC_GRADING_ENGINE.md) — the Celery pipeline DAG, per-criterion metric formulations, the LLM Rubric Judge's schemas/prompt, and self-consistency reconciliation.
- [`docs/SPEC_04_REPOSITORY_AND_BUILD_PLAN.md`](docs/SPEC_04_REPOSITORY_AND_BUILD_PLAN.md) — monorepo structure and the nine-phase build sequence this system was actually built against.
- [`docs/adr/0001-raw-websocket-gemini-bridge.md`](docs/adr/0001-raw-websocket-gemini-bridge.md) — the one recorded architecture decision: why `gemini_bridge.py` speaks raw WebSocket JSON instead of the `google-genai` SDK.

## Build history — nine phases

Each phase's exit criteria were proven (via CI-checkable tests wherever
feasible) before the next phase began:

| Phase | Delivered |
|---|---|
| **0 — Foundations** | Repo scaffold, CI pipeline, Docker Compose dev stack, base Postgres schema, auth skeleton |
| **1 — Media Spine** | Browser mic/cam capture, `AudioWorkletProcessor` PCM16 encoding, WS gateway skeleton, raw PCM → object storage |
| **2 — Gemini Live Bridge** | `gemini_bridge.py`, PTT-driven `activityStart`/`activityEnd`, session resumption, fixture-replay test harness |
| **3 — Exam FSM Core** | `packages/exam-fsm`, wired into the gateway, Part 2 timer watchdogs, phase-directive injection |
| **4 — Persistence & Resiliency** | Event-sourced `exam_session_events` as full source of truth, reconnect/resume flow, disconnect fairness policy |
| **5 — Backline Transcription** | Celery/RabbitMQ skeleton, `finalize_media` + `transcribe_full_session`, word-level transcripts |
| **6 — Feature Extraction** | All four `compute_*` criteria tasks, golden-file-tested against reference transcripts |
| **7 — LLM Rubric Judge & Report Synthesis** | `synthesize_band_scores`, licensed rubric-asset loading, self-consistency reconciliation, `band_score_reports` |
| **8 — Hardening & Load** | OpenTelemetry/Prometheus latency profiling, a concurrency load-testing harness, a security/compliance audit, frontend accessibility pass |
| **9 — Pilot & Calibration** | Batch shadow-scoring against a certified benchmark corpus, statistical rater-vs-judge agreement metrics, a tuning feedback loop for the judge prompt and reconciliation/confidence thresholds |

Full phase-by-phase build/exit-criteria detail: `docs/SPEC_04_REPOSITORY_AND_BUILD_PLAN.md` §2.

## Quickstart

```bash
# 1. Shared infrastructure (Postgres, Redis, RabbitMQ, MinIO, Prometheus, Grafana)
docker-compose -f infra/docker/docker-compose.dev.yml up -d

# 2. Python dependencies (editable installs — packages/exam-fsm is a sibling dependency)
pip install -e packages/exam-fsm
pip install -e "apps/api-gateway[dev]"
pip install -e "apps/worker[dev]"

# 3. Apply the shared schema
alembic -c migrations/alembic.ini upgrade head

# 4. Run the gateway
cd apps/api-gateway && uvicorn app.main:app --reload

# 5. Run the Celery worker (separate shell)
cd apps/worker && celery -A celery_app worker --loglevel=info

# 6. Run the frontend (separate shell)
cd apps/web && npm install && npm run dev
```

Every setting has a working, insecure-by-design local-dev default — see
each app's README for the full configuration reference and which secrets
must be rotated before any non-local deployment (`GEMINI_API_KEY`,
`DEEPGRAM_API_KEY`, `AZURE_SPEECH_KEY`, `ANTHROPIC_API_KEY`, `JWT_SECRET`,
`INTERNAL_DEBUG_TOKEN`, and the licensed rubric asset —
[`packages/grading-rubric-assets/README.md`](packages/grading-rubric-assets/README.md)).

## Testing

```bash
ruff check apps/api-gateway apps/worker packages tests

pytest packages/exam-fsm/tests tests/unit apps/api-gateway/app/tests apps/worker/tests   # unit
pytest tests/integration                                                                  # real Postgres/MinIO, fixture vendors
pytest tests/load                                                                         # load-harness smoke (not a real load test)

cd apps/web && npm run lint && npx tsc --noEmit && npm test                               # frontend, not yet CI-wired
```

No test in this repository ever dials out to a real vendor (Gemini,
Deepgram, Azure, Claude) — every vendor integration is exercised through a
deterministic fixture or fake standing in behind the same interface the
real implementation satisfies. Full testing-pyramid detail, fixture
inventory, and CI wiring: [`tests/README.md`](tests/README.md).

## Known gaps (stated honestly, not silently)

- **`packages/shared-schemas`** was scaffolded but never populated — every
  cross-app payload shape ended up duplicated per consumer instead. See
  that package's README for the full accounting and what unifying it
  would require.
- **`infra/k8s/` and `infra/terraform/`** are empty placeholders — no
  phase in the build plan scoped production container orchestration or
  cloud infrastructure provisioning; only the local dev stack and the
  Phase 8 observability stack were built. See
  [`infra/README.md`](infra/README.md) for exactly what a real deployment
  would still need.
- **`tests/e2e/`** is empty — no browser-driven end-to-end suite exists;
  the closest coverage is a protocol-level WebSocket integration test that
  walks the full exam FSM. See [`tests/README.md`](tests/README.md).
- **The frontend Vitest suite is not wired into CI** — `.github/workflows/ci.yml`
  is Python-only today.

## License / IP notice

The official IELTS band-descriptor text is Cambridge/IDP/British Council
licensed intellectual property and is **never** committed to this
repository (see [`packages/grading-rubric-assets/README.md`](packages/grading-rubric-assets/README.md)).
All prompt/persona content in `packages/prompt-templates/` is original to
this project.
