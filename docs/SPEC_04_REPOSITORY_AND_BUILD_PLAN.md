# SPEC_04 — Repository & Build Plan
### Virtual IELTS Speaking Examination Platform

| | |
|---|---|
| **Status** | Approved for build (v1.0) |
| **Depends on** | `SPEC_01`, `SPEC_02`, `SPEC_03` |
| **Scope** | Concrete repo layout and a phased, regression-safe build sequence |

---

## 1. Repository Structure

A single monorepo, three deployable apps, shared packages between them. The boundary between `apps/` and `packages/` is deliberate: anything under `packages/` must be independently unit-testable with no network dependency, which is what makes the phased build plan in §2 actually safe.

```
ielts-speaking-platform/
├── apps/
│   ├── web/                                # Next.js frontend
│   │   ├── src/
│   │   │   ├── app/
│   │   │   │   ├── (auth)/
│   │   │   │   ├── exam/[sessionId]/       # the exam room route
│   │   │   │   └── results/[sessionId]/
│   │   │   ├── components/
│   │   │   │   ├── exam/
│   │   │   │   │   ├── PTTButton.tsx
│   │   │   │   │   ├── CueCardPanel.tsx
│   │   │   │   │   ├── PrepCountdown.tsx
│   │   │   │   │   ├── CaptionStream.tsx
│   │   │   │   │   └── ConnectionStatusBanner.tsx
│   │   │   │   └── ui/                     # shadcn/ui primitives
│   │   │   ├── audio/
│   │   │   │   ├── pcm-worklet-processor.ts   # AudioWorkletProcessor, PCM16 @16kHz
│   │   │   │   ├── playback-jitter-buffer.ts
│   │   │   │   └── proctoring-recorder.ts     # MediaRecorder wrapper, video+audio
│   │   │   ├── ws/
│   │   │   │   ├── exam-socket-client.ts
│   │   │   │   └── reconnect-manager.ts       # resume_token handling, IndexedDB replay buffer
│   │   │   ├── state/
│   │   │   │   └── examStore.ts               # Zustand — mirrors server-pushed FSM state only
│   │   │   └── lib/
│   │   └── package.json
│   │
│   ├── api-gateway/                        # FastAPI — REST + Live WS bridge
│   │   ├── app/
│   │   │   ├── main.py
│   │   │   ├── routers/
│   │   │   │   ├── auth.py
│   │   │   │   ├── sessions.py             # session create/resume REST endpoints
│   │   │   │   ├── results.py
│   │   │   │   └── ws_exam.py              # the live WebSocket endpoint
│   │   │   ├── services/
│   │   │   │   ├── gemini_bridge.py        # Live API session mgmt, directive injection,
│   │   │   │   │                           # force_mute_input, session_resumption handling
│   │   │   │   ├── fsm_engine.py           # imports packages/exam-fsm
│   │   │   │   ├── media_tap.py            # fan-out: object storage + ASR streaming tap
│   │   │   │   ├── presigned_upload.py
│   │   │   │   └── timers.py               # Redis-backed absolute-deadline watchdogs
│   │   │   ├── models/                     # SQLAlchemy models
│   │   │   └── deps.py
│   │   └── pyproject.toml
│   │
│   └── worker/                             # Celery app — grading pipeline
│       ├── celery_app.py
│       ├── tasks/
│       │   ├── media.py                    # finalize_media
│       │   ├── asr.py                      # transcribe_full_session
│       │   ├── nlp/
│       │   │   ├── fluency.py              # compute_fluency_metrics
│       │   │   ├── lexical.py              # compute_lexical_metrics
│       │   │   └── grammar.py              # compute_grammar_metrics
│       │   ├── pronunciation.py            # compute_pronunciation_scores + GOP fallback
│       │   └── scoring.py                  # synthesize_band_scores, reconciliation
│       ├── pipelines/
│       │   └── grading_pipeline.py         # the chain/group/chord wiring (Spec 03 §2)
│       └── pyproject.toml
│
├── packages/
│   ├── shared-schemas/                     # Pydantic (Python) + generated TS types
│   │   ├── python/
│   │   └── typescript/
│   ├── exam-fsm/                           # PURE state machine — no I/O, fully unit-testable
│   │   ├── states.py
│   │   ├── transitions.py
│   │   └── tests/
│   ├── prompt-templates/                   # Versioned Gemini system instructions + directives
│   │   ├── base_persona_v1.txt
│   │   ├── directives/
│   │   │   ├── part2_prep.txt
│   │   │   ├── part2_warn.txt
│   │   │   ├── part2_hard_stop.txt
│   │   │   └── ...
│   │   └── CHANGELOG.md
│   └── grading-rubric-assets/              # licensed content — secrets-managed, not public repo
│       └── .gitkeep                        # actual asset injected via secret store at deploy time
│
├── infra/
│   ├── docker/
│   │   ├── docker-compose.dev.yml          # Postgres, Redis, RabbitMQ, MinIO, LanguageTool
│   │   ├── Dockerfile.web
│   │   ├── Dockerfile.api-gateway
│   │   └── Dockerfile.worker
│   ├── k8s/
│   │   ├── gateway-statefulset.yaml        # session-affinity, PodDisruptionBudget
│   │   ├── api-deployment.yaml
│   │   ├── worker-deployment.yaml
│   │   └── redis / rabbitmq / postgres manifests or managed-service refs
│   └── terraform/
│       ├── networking/
│       ├── storage/                        # S3 buckets per Spec 01 §7
│       └── secrets/
│
├── migrations/                             # Alembic
│
├── tests/
│   ├── unit/                               # packages/* — pure logic
│   ├── integration/                        # api-gateway against a Gemini Live *fixture/replay* harness
│   ├── e2e/                                # Playwright: full exam room flow against docker-compose stack
│   └── fixtures/
│       ├── gemini_live_replay/             # recorded session transcripts for deterministic bridge testing
│       └── reference_audio/                # hand-labeled audio for feature-extraction golden tests
│
└── docs/
    ├── SPEC_01_SYSTEM_ARCHITECTURE.md
    ├── SPEC_02_IELTS_FLOW_STATE_MACHINE.md
    ├── SPEC_03_ASYNC_GRADING_ENGINE.md
    ├── SPEC_04_REPOSITORY_AND_BUILD_PLAN.md
    └── adr/                                 # Architecture Decision Records for anything that
                                              # deviates from these specs later
```

---

## 2. Phased Build Sequence

The ordering is deliberate: **prove the media plumbing before any AI touches it, prove the AI loop before the exam logic wraps it, prove the exam logic before grading depends on it.** Each phase has explicit exit criteria — no phase starts before the previous one's exit criteria are demonstrably met, because every later phase assumes the earlier ones are load-bearing infrastructure, not prototypes to revisit.

### Phase 0 — Foundations
**Build:** repo scaffold, CI pipeline, Docker Compose dev stack (Postgres, Redis, RabbitMQ, MinIO), base Postgres schema + Alembic migration zero, auth skeleton (candidate login/session creation, no exam logic yet).
**Exit criteria:** `docker-compose up` gives a working empty stack; CI runs lint + a trivial test on every PR; a candidate can log in and create a session row.

### Phase 1 — The Media Spine
**Build:** browser mic/cam capture, `AudioWorkletProcessor` PCM16 encoding, WS gateway skeleton (no Gemini yet), raw PCM persisted to MinIO/S3, a trivial loopback test (server echoes audio back to prove the full capture → transport → storage → playback path works end to end).
**Exit criteria:** a candidate can speak into the browser and hear their own voice echoed back with acceptable latency, and the raw audio lands correctly in object storage with correct sequence/checksum metadata. **No AI has been touched yet — this phase is deliberately "boring" so that any future bug is provably not a media-plumbing bug.**

### Phase 2 — Gemini Live Bridge
**Build:** `gemini_bridge.py` — connect the gateway to the Live API, implement PTT-driven `activityStart`/`activityEnd` (VAD disabled per Spec 01 §4.1), a minimal single-turn scripted conversation ("say hello back"), and session-resumption handling (`SessionResumptionConfig`, handle storage, `GoAway` handling).
**Exit criteria:** a candidate can hold PTT, speak, release, and hear a live Gemini audio reply within the latency budget (Spec 01 §4.4); killing and resuming the browser connection mid-conversation resumes without re-establishing context from scratch; a recorded-fixture replay test (against `tests/fixtures/gemini_live_replay/`) passes in CI without hitting the real API.

### Phase 3 — Exam FSM Core
**Build:** `packages/exam-fsm` fully implemented and unit-tested in isolation (pure logic, no I/O) against every transition in Spec 02 §1; wire it into `api-gateway` first against **mocked** Gemini responses (fixture-driven), then against the real Live bridge from Phase 2; implement Part 2's prep/cutoff timer watchdogs (Spec 02 §3.3) and phase-directive injection (Spec 02 §6.2).
**Exit criteria:** a full scripted exam session (Intro → Part 1 → Part 2 with real 60s/120s timers → Part 3 → Close) completes end-to-end against the real Gemini Live API in a staging environment, with correct phase transitions logged as events.

### Phase 4 — Persistence & Resiliency
**Build:** event-sourced `exam_session_events` fully wired as the state source of truth (Spec 01 §5.2); reconnect/resume flow (§5.3) implemented for both the "within resumption window" and "expired, re-seed from event log" paths; timer-pause-on-disconnect fairness policy (§5.4).
**Exit criteria:** a chaos-test suite that kills the browser connection, the gateway pod, and the Gemini WS independently, at random points across all exam phases, and asserts the session always resumes to the correct phase with correct remaining timer state and no duplicated or lost audio segments.

### Phase 5 — Backline Transcription & Storage
**Build:** Celery + RabbitMQ skeleton stood up; `finalize_media` and `transcribe_full_session` tasks (Spec 03 §2–3); word-level transcripts persisted to Postgres.
**Exit criteria:** closing a real exam session from Phase 3/4 automatically produces a canonical stitched audio file and a word-level timestamped transcript in the database, visible via an internal debug endpoint, within the target turnaround (Spec 01 §9).

### Phase 6 — Feature Extraction Pipelines
**Build:** `compute_fluency_metrics`, `compute_lexical_metrics`, `compute_grammar_metrics`, `compute_pronunciation_scores` (with the Azure/GOP fallback gate) — each built and **unit-tested independently** against `tests/fixtures/reference_audio/` (hand-labeled audio with known expected metric ranges, e.g. a known-fluent sample should score above a known-hesitant sample on MLR).
**Exit criteria:** each of the four feature tasks passes its golden-file tests in isolation; running all four against a real Phase-5 transcript produces plausible, internally consistent `FeatureVector` JSON with correct provenance tags.

### Phase 7 — LLM Rubric Judge & Report Synthesis
**Build:** `synthesize_band_scores` with the schema-validated `ScoringLLM` interface (Spec 03 §5), the licensed rubric-asset loading path, self-consistency reconciliation, report PDF/summary generation, human-review flagging.
**Exit criteria:** a full session run through Phases 3–7 produces a complete `band_score_reports` row with justifications that correctly cite specific computed features (spot-checked against a small human-rated benchmark set); disagreement injection tests (deliberately feeding conflicting feature vectors) correctly trigger `flag_for_human_review`.

### Phase 8 — Hardening & Load
**Build:** latency profiling against the Spec 01 §4.4 budget table under realistic concurrency; load test toward the target concurrent-session ceiling (Spec 01 §9); security review (credential handling, presigned URL scoping, PII retention paths); accessibility pass on the frontend exam room.
**Exit criteria:** P95 first-audible-reply latency meets budget under target concurrent load; no critical findings from the security review remain open; the exam room meets the project's accessibility bar.

### Phase 9 — Pilot & Calibration
**Build:** shadow-score a benchmark set of real (or simulated) sessions against certified human IELTS rater scores; calibrate the Judge prompt, confidence thresholds, and reconciliation band-disagreement threshold (Spec 03 §5.6) based on observed agreement; formal sign-off.
**Exit criteria:** Judge-vs-human agreement on the benchmark set meets the product's accuracy bar (defined jointly with the assessment/psychometrics stakeholder, not engineering alone); pipeline is approved to score real candidates without a mandatory human-review gate on every session.

---

## 3. Anti-Regression Discipline

- **`packages/exam-fsm` is pure and exhaustively unit-tested** precisely so that exam-logic bugs can never hide behind "was it the network or was it the state machine" ambiguity — if a transition test fails, it is a logic bug, full stop.
- **`tests/fixtures/gemini_live_replay/` decouples CI from the live vendor API.** The Live bridge is tested against recorded session fixtures in CI on every PR; a small, separate nightly job runs against the real API to catch vendor-side drift, kept deliberately out of the PR-blocking path so vendor flakiness never blocks a merge.
- **Feature-extraction golden tests (`tests/fixtures/reference_audio/`) pin expected metric *ranges*, not exact values** — acoustic/NLP pipelines will legitimately shift slightly with library version bumps; the tests exist to catch gross regressions (a fluent sample suddenly scoring as hesitant), not to freeze the pipeline against any change at all.
- **Every phase's exit criteria in §2 is a CI-checkable gate where feasible** (chaos tests, golden-file tests, replay-fixture tests), not just a manual sign-off, so that Phase *N* work cannot silently regress Phase *N-1*'s guarantees as the codebase grows.
