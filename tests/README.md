# `tests`

The repo-root test suites: integration tests, the load-testing harness,
shared fixtures, and shared test-support infrastructure. This directory
complements — it does not replace — the **co-located unit test suites**
that live inside each Python package/app (`packages/exam-fsm/tests/`,
`apps/api-gateway/app/tests/`, `apps/worker/tests/`). Together they form
the testing pyramid this monorepo actually runs in CI, per Spec 04 §3's
"Anti-Regression Discipline."

## The testing pyramid

```
                    ┌─────────────────────────┐
                    │   tests/load/            │  Real-infra, low-concurrency
                    │   (harness smoke only)   │  CI smoke; real load runs are
                    └─────────────────────────┘  manual and opt-in
                    ┌─────────────────────────┐
                    │   tests/integration/     │  Real Postgres + MinIO;
                    │   (30 files)             │  Gemini/Deepgram/Azure/OpenAI
                    └─────────────────────────┘  always fixture/fake — never real
        ┌───────────────────────────────────────────────┐
        │  packages/exam-fsm/tests/  apps/api-gateway/    │  No real infra;
        │  app/tests/  apps/worker/tests/  tests/unit/     │  fastest, run first
        └───────────────────────────────────────────────┘
```

| Layer | Location | Needs | CI step |
|---|---|---|---|
| Unit | `packages/exam-fsm/tests/`, `apps/api-gateway/app/tests/`, `apps/worker/tests/`, `tests/unit/` | Nothing | `Unit tests` |
| Integration | `tests/integration/` | Real Postgres + MinIO (RabbitMQ/Redis for the pipeline-trigger paths) | `Integration tests` |
| Load harness | `tests/load/` | Spawns a real `uvicorn` subprocess; fake Gemini server | `Load harness smoke test` |
| E2E | `tests/e2e/` | **Not implemented** — see below | Not in CI |

`.github/workflows/ci.yml` runs, in this exact order, after `ruff check`
and `alembic upgrade head`:

```bash
pytest packages/exam-fsm/tests tests/unit apps/api-gateway/app/tests apps/worker/tests
pytest tests/integration
pytest tests/load
```

The root `pyproject.toml`'s `[tool.pytest.ini_options]` sets
`testpaths = ["tests", "packages"]` and `asyncio_mode = "auto"` — so a
bare `pytest` from the repo root discovers `tests/` and
`packages/exam-fsm/tests/` automatically (the two app-local suites under
`apps/*/app/tests` and `apps/worker/tests` are outside those `testpaths`
and are invoked explicitly, as CI does above).

## Directory structure

```
tests/
├── unit/
│   └── test_scaffold.py                  Phase 0's trivial CI-pipeline sanity gate
├── integration/                          30 files — see table below
├── load/
│   ├── live_session_load.py              The concurrency load-testing harness itself
│   └── test_load_harness_smoke.py        Low-concurrency pytest wrapper, CI-safe
├── support/
│   └── fake_gemini_live_server.py        Shared fixture-replay fake Gemini Live WS server
├── fixtures/
│   ├── gemini_live_replay/               Recorded Gemini session fixtures (connectivity check, full exam walk)
│   ├── reference_audio/                  Golden transcript fixtures for Phase 6 feature-extraction tests
│   ├── rubric_assets/                    Test-only placeholder rubric descriptor text
│   └── calibration_benchmark/            Phase 9's synthetic benchmark corpus
└── e2e/                                  Empty — reserved, never populated (see below)
```

## Cross-app import convention

**No `conftest.py` exists anywhere in this repository.** Every
integration test file instead does its own explicit `sys.path.insert(0, ...)`
at the top, pointing at whichever app directories it needs
(`apps/api-gateway`, `apps/worker`, `tests/support`):

```python
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "apps" / "api-gateway"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "apps" / "worker"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "support"))
```

followed by `# noqa: E402` on every import that must come after the path
manipulation. This is a deliberate, repo-wide convention (not an
oversight) — it mirrors how the real deployed processes resolve
cross-module imports (Celery's `-A` resolution inserts `cwd`; see
[`apps/worker/README.md`](../apps/worker/README.md#running-locally)), so
a passing test is evidence the same import shape works in the actual
runtime, not just under a test-only path-mangling shim.

## The fixture-replay pattern (`tests/support/fake_gemini_live_server.py`)

`FakeGeminiLiveServer` / `FakeGeminiLiveServerHandle` is a ~100-line local
`websockets.serve()` that replays a recorded JSON fixture
(`tests/fixtures/gemini_live_replay/*.json`) verbatim, dispatching on the
incoming message's top-level key (`setup` → `after_setup` responses,
`clientContent` → `after_directive` responses, `activityEnd` →
`after_activity_end` responses). `FakeGeminiLiveServerHandle` runs it on
its own thread + event loop so it's reachable from a `TestClient`-driven
FastAPI app running on a different loop/portal thread.

**No integration test in this repository ever dials out to the real
Gemini Live API, Deepgram, Azure, or OpenAI.** Every vendor call is either
this fixture-replay server (Gemini) or an injected fixture/fake provider
satisfying the real provider's interface (`FixtureTranscriptionProvider`-
style doubles for Deepgram/WhisperX, `FixtureGrammarCheckProvider` for
LanguageTool, `FixturePronunciationProvider` for Azure/GOP,
`FixtureScoringLLM`/`CorpusScriptedScoringLLM` for OpenAI). This is what
`docs/SPEC_04_REPOSITORY_AND_BUILD_PLAN.md` §3 means by "vendor flakiness
never blocks a merge" — real-vendor validation is a manual/nightly
concern, never a PR-blocking one.

## `tests/fixtures/` inventory

| Subdirectory | Contents | Consumed by |
|---|---|---|
| `gemini_live_replay/` | `connectivity_test_session.json` (a minimal single-turn script), `full_exam_session.json` (the entire `INIT_DEVICE_CHECK → COMPLETE` walk) | `test_gemini_bridge_replay.py`, `test_exam_room_gemini_relay.py`, `test_full_exam_session_flow.py`, `test_observability_metrics.py`, `test_no_credential_leakage.py`, `tests/load/test_load_harness_smoke.py` |
| `reference_audio/` | Hand-authored transcript JSON pairs contrasting fluency/lexical/grammar profiles (`fluent_transcript.json` vs. `hesitant_transcript.json`, `rich_lexicon_transcript.json` vs. `simple_lexicon_transcript.json`, `complex_grammar_transcript.json` vs. `simple_grammar_transcript.json`) | `test_fluency_metrics.py`, `test_lexical_metrics.py`, `test_grammar_metrics.py` — golden-file tests that pin metric **ranges**, not exact values (Spec 04 §3: acoustic/NLP pipelines legitimately shift slightly across library version bumps; these tests exist to catch gross regressions, e.g. a fluent sample suddenly scoring as hesitant, not to freeze the pipeline against any change at all) |
| `rubric_assets/` | `band_descriptors_v1.json` — a clearly-labeled, self-authored **test-only placeholder**, explicitly not official IELTS wording | `test_synthesize_band_scores.py`, `test_calibration_pilot.py` |
| `calibration_benchmark/` | `benchmark_corpus_v1.json` — 8 synthetic simulated sessions with certified-gold `human_scores` and two scripted judge passes each | `test_calibration_pilot.py`, and `apps/worker/tasks/calibration.py`'s CLI default corpus |

## `tests/integration/` — full inventory

| File | Exercises |
|---|---|
| `test_auth_session_flow.py` | Login → session creation REST flow |
| `test_video_presigned_upload.py` | Presigned video upload URL issuance + confirmation |
| `test_gemini_bridge_replay.py` | `gemini_bridge.py` protocol-level, no FastAPI |
| `test_exam_room_gemini_relay.py` | One real PTT turn through `/ws/exam/{id}`, latency/media persistence assertions |
| `test_full_exam_session_flow.py` | The entire Spec 02 §1 phase sequence against the fixture-replay server |
| `test_no_credential_leakage.py` | Asserts no vendor secret ever appears in a client-facing WS frame or `/metrics` |
| `test_observability_metrics.py` | Asserts the Phase 8 latency histograms actually record after a real PTT turn |
| `test_internal_debug_endpoint.py` | `/internal/sessions/{id}/transcript` |
| `test_grading_trigger.py` | `enqueue_grading()`'s producer-only Celery send |
| `test_finalize_media.py` | Canonical FLAC stitching from real per-turn WAVs |
| `test_transcribe_full_session.py` | Backline transcription against a fixture transcription provider |
| `test_feature_extraction_pipeline.py` | All four `compute_*` tasks against one real Phase-5 transcript |
| `test_fluency_metrics.py` / `test_lexical_metrics.py` / `test_grammar_metrics.py` / `test_pronunciation_scores.py` | Golden-file range-pinned tests per criterion |
| `test_synthesize_band_scores.py` | Consistent-passes and conflicting-passes → `flag_for_human_review` |
| `test_media_retention_sweep.py` | `sweep_expired_raw_audio` against real MinIO |
| `test_calibration_pilot.py` | The full 8-case Phase 9 benchmark corpus end-to-end |

## `tests/load/` — the concurrency harness

`live_session_load.py` is a standalone asyncio script (not itself a
pytest suite) that logs in N simulated candidates against a **running**
gateway process, drives repeated realistic PTT turn cycles, and reports
P50/P95/P99/max release-to-first-audio latency plus error/drop counts.
Explicitly **not** a real load test at Spec 01 §9's ~1,000-concurrent-
session ceiling — see the module's own docstring for why CI only ever
runs it at low concurrency:

```bash
# CI-safe smoke (asserts the harness mechanism works, ~5 sessions, few seconds):
pytest tests/load

# A genuine, opt-in stress run against a properly scaled deployment:
python tests/load/live_session_load.py --concurrency 200 --duration 60 --json-out report.json
python tests/load/live_session_load.py --real-gemini ...   # spends real vendor quota — deliberate, not the default
```

`test_load_harness_smoke.py` spawns a real `uvicorn` subprocess (genuine
concurrent TCP connections, unlike an in-process `TestClient`) against the
fixture-replay fake Gemini server and asserts zero errors/dropped
connections at `--concurrency 5`.

## `tests/e2e/` — reserved, not populated

This directory is empty — not even a `.gitkeep`, and nothing references
it in `.github/workflows/ci.yml`. `CLAUDE.md`'s repository tree lists
"Unit, Integration (Gemini Replay Fixtures), and E2E" under `tests/`, but
no phase in `docs/SPEC_04_REPOSITORY_AND_BUILD_PLAN.md` scoped a true
browser-driven end-to-end suite (e.g. Playwright driving `apps/web`
against a real running gateway). The closest existing coverage is
`tests/integration/test_full_exam_session_flow.py`, which exercises the
full exam FSM end-to-end at the **WebSocket protocol level** through the
real FastAPI route — a genuine integration test, not a browser-level E2E
test. Stated here explicitly so this gap is discoverable rather than
silently assumed covered.

## Frontend tests (not part of this directory)

`apps/web`'s Vitest suite (component + accessibility tests) is co-located
under `apps/web/src/components/exam/*.test.tsx`, not here, and is **not**
currently wired into `.github/workflows/ci.yml` (a Python-only pipeline
today). See [`apps/web/README.md`](../apps/web/README.md#testing) for how
to run it.

## Cross-references

- Anti-regression discipline this directory's conventions implement: `docs/SPEC_04_REPOSITORY_AND_BUILD_PLAN.md` §3
- CI-vendor-decoupling rationale for the fixture-replay pattern: `docs/SPEC_04_REPOSITORY_AND_BUILD_PLAN.md` §3, [`docs/adr/0001-raw-websocket-gemini-bridge.md`](../docs/adr/0001-raw-websocket-gemini-bridge.md)
- Non-functional load targets the load harness measures against: `docs/SPEC_01_SYSTEM_ARCHITECTURE.md` §9
