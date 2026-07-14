# `apps/api-gateway`

Stateful WebSocket gateway and REST API for the Virtual IELTS Speaking
Examination Platform. This service is the **only** component that ever
holds a live Gemini Live connection or a live candidate WebSocket in
memory, and the **only** component authorized to advance exam state. It
implements the server-authoritative control plane described in
[`docs/SPEC_01_SYSTEM_ARCHITECTURE.md`](../../docs/SPEC_01_SYSTEM_ARCHITECTURE.md)
and drives the exam state machine defined in
[`docs/SPEC_02_IELTS_FLOW_STATE_MACHINE.md`](../../docs/SPEC_02_IELTS_FLOW_STATE_MACHINE.md).

Everything downstream — asynchronous grading (`apps/worker`), the browser
client (`apps/web`) — treats this service's outputs (Postgres rows, object
storage keys, WebSocket messages) as the system of record. Nothing in this
service performs LLM-based grading; it only orchestrates the live
conversation, persists raw evidence, and enqueues the grading pipeline on
completion (CLAUDE.md rule 1).

## Architectural role

```
Browser (apps/web)
   │  WSS + REST (JWT bearer)
   ▼
apps/api-gateway  ◄────────────────────────────┐
   │  raw WebSocket JSON (BidiGenerateContent)   │ shared Postgres (async)
   ▼                                             │ shared Redis
Gemini Live API                                  │ shared S3/MinIO
                                                  │
   │  finalize/enqueue on COMPLETE                │
   ▼                                              │
apps/worker (Celery)  ─────────────────────────────┘
```

Three non-negotiable properties, enforced structurally by this codebase
(CLAUDE.md rules 1–3, 5):

1. **Server-authoritative control.** The client never decides its own
   phase transitions, timer expirations, or turn boundaries — it renders
   what the gateway pushes and forwards raw input. Every phase change goes
   through `app/services/fsm_engine.py`, which is the single call site for
   `packages/exam-fsm`'s pure `transition()` function.
2. **Push-to-Talk, not VAD.** Gemini's built-in voice-activity detection
   is explicitly disabled at connection setup
   (`app/services/gemini_bridge.py::connect`,
   `realtimeInputConfig.automaticActivityDetection.disabled = True`).
   Turn boundaries are driven exclusively by the client's `activity_start`
   / `activity_end` control messages.
3. **Event-sourced resiliency.** Current FSM phase is never stored as a
   mutable "current state" field alone — `exam_sessions.current_phase` is
   a cache; the durable source of truth is the append-only
   `exam_session_events` table, replayed via `packages/exam-fsm`'s pure
   `fold()` function. A pod failover or browser reload resumes by querying
   the log, not by trusting in-memory state.

## Directory structure

```
apps/api-gateway/
├── app/
│   ├── main.py                  FastAPI app assembly, router mounting, /metrics, /healthz
│   ├── config.py                Pydantic Settings — every configuration knob (see below)
│   ├── db.py                    Async SQLAlchemy engine/session factory, declarative Base
│   ├── deps.py                  FastAPI dependency: JWT bearer -> Candidate
│   ├── models/                  SQLAlchemy ORM models (one file per table)
│   ├── routers/                 FastAPI routers (REST + the one WS route)
│   ├── services/                Business logic: FSM engine, Gemini bridge, orchestrator, timers, media, observability
│   └── tests/                   Unit tests (no real Postgres/Redis/S3/Gemini)
├── .env.example                 Documented environment variable template
└── pyproject.toml                Dependencies, ruff config, package discovery
```

### `app/models/` — SQLAlchemy schema (async side)

This app owns the canonical, authoritative definition of the shared
Postgres schema (`app/db.py::Base`); `migrations/env.py` imports
`app.models` directly to generate Alembic migrations, and `apps/worker`
maintains its own read/write **sync** mirror of the subset of tables it
touches (see [`apps/worker/README.md`](../worker/README.md#shared-schema-ownership)
for why that duplication exists rather than a shared package).

| Model | Table | Purpose |
|---|---|---|
| `Candidate` | `candidates` | Identity; `id_verification_hash` only — no raw ID image is ever persisted (Spec 01 §6/§8) |
| `ExamSession` | `exam_sessions` | Durable session identity, coarse `status`, `current_phase` snapshot pointer, `resume_token`, `gemini_resumption_handle`, bound `cue_card_id`/`topic_set_ids` |
| `ExamSessionEvent` | `exam_session_events` | Append-only, `UNIQUE(session_id, seq)` — the event-sourced FSM log; **never updated or deleted** |
| `AudioSegment` | `audio_segments` | Per-turn raw candidate PCM pointer + checksum/byte_size + captured `exam_phase` (Spec 03 §4 per-phase bucketing) |
| `VideoSegment` | `video_segments` | Proctoring video pointer only — **never imported by `apps/worker`**, which is what architecturally guarantees video can't leak into grading (CLAUDE.md rule 3) |
| `TopicSet` | `topic_sets` | Rotating Part 1 topic bank, one slot (A/B/C) per row |
| `CueCard` | `cue_cards` | Versioned, deterministic Part 2 cue card bank |
| `GradingJob` | `grading_jobs` | Read-only mirror here — only `apps/worker` writes to it; exposed via `/internal/sessions/{id}/transcript` |
| `Transcript` | `transcripts` | Read-only mirror here — only `apps/worker`'s `transcribe_full_session` writes to it |

### `app/routers/` — HTTP & WebSocket surface

| Router | Prefix | Auth | Purpose |
|---|---|---|---|
| `auth.py` | `/auth` | none | `POST /auth/login` — lookup-or-create candidate by email, issue a JWT. No ID-verification flow (out of scope, Phase 0). |
| `sessions.py` | `/sessions` | JWT bearer | `POST /sessions` (creates the durable session + `SESSION_CREATED` event), `GET /sessions/{id}`, `POST /sessions/{id}/video-upload-url` (presigned PUT), `POST /sessions/{id}/video-upload-complete` |
| `ws_exam.py` | `/ws/exam/{session_id}` | JWT via `?token=` query param | **The live exam room.** One WebSocket connection = one live Gemini bridge + one `ExamOrchestrator` instance. See below. |
| `internal_debug.py` | `/internal` | shared `X-Internal-Debug-Token` header | `GET /internal/sessions/{id}/transcript` — ops/QA visibility into `grading_jobs`/`transcripts`, not candidate-facing |

`main.py` also mounts `/metrics` (Prometheus scrape target,
`app/services/observability.py`) and `/healthz`.

### `app/services/` — the actual engineering

| Module | Responsibility |
|---|---|
| `gemini_bridge.py` | Raw `websockets` client speaking Google's `BidiGenerateContent` protocol directly (not the `google-genai` SDK — see [ADR 0001](../../docs/adr/0001-raw-websocket-gemini-bridge.md)). Owns connect/setup, PTT-bounded audio forwarding, `[EXAMINER_DIRECTIVE]` injection, input muting, and typed decoding of server events (`AudioDelta`, `TranscriptDelta`, `TurnComplete`, `Interrupted`, `SessionResumptionUpdate`, `GoAway`). Has no opinion about exam phases or timers. |
| `fsm_engine.py` | The **only** call site for `packages/exam-fsm`'s pure `transition()`/`fold()`. Owns all I/O against `exam_session_events`/`exam_sessions.current_phase`. Raises the pure package's `InvalidTransitionError` uncaught — always a real caller bug, never swallowed. |
| `exam_orchestrator.py` | Per-connection phase driver (`ExamOrchestrator`). Turns FSM transitions into actual exam behavior: directive injection on phase entry, cue-card/topic-set content selection, Part 2 timer watchdog lifecycle, turn-budget-based soft-phase exits, `timer_deadline` pushes to the client, and enqueuing the grading pipeline on `COMPLETE`. |
| `timers.py` | Redis-backed **absolute-deadline** watchdogs for Part 2's hard 60s prep / 120s speaking cutoff (Spec 02 §3.3). Deadlines are stored as epoch timestamps, never "seconds remaining," so a resumed connection picks up exactly where it left off instead of resetting. Only detects expiry + performs bridge-level mute/directive side effects — never mutates FSM state directly. |
| `media_tap.py` | Wraps a completed turn's raw PCM16 buffer as WAV, uploads to `raw-audio/{session_id}/segments/{turn_id}_{seq}.wav`, and now also owns `configure_bucket_lifecycle()` (Spec 01 §7 retention enforcement, Phase 8). |
| `presigned_upload.py` | Issues a 10-minute presigned PUT URL for the proctoring video track — the browser uploads directly to object storage, never through this API pod. |
| `grading_trigger.py` | A lightweight, **producer-only** Celery client (`send_task` by name) that enqueues `grading.grade_exam_session` on session completion. Deliberately does not import `apps/worker`'s task modules (avoids pulling spaCy/whisperx/etc. into this process). |
| `exam_content.py` | I/O-bearing content selection (cue card draw, Part 1 topic-set assignment avoiding a candidate's history) — deliberately kept out of `packages/exam-fsm`, which must stay pure. |
| `observability.py` | OpenTelemetry spans + Prometheus histograms for the Spec 01 §4.4 latency budget. See **Observability** below. |

### The live exam room (`ws_exam.py`) — request lifecycle

1. **Auth**: `token` query param is JWT-verified against the session's
   `candidate_id`; any stored `gemini_resumption_handle` is loaded.
2. **Connect**: a `GeminiLiveBridge` opens the Live WS (with the
   resumption handle if present, for transparent server-side context
   restore — Spec 01 §5.3).
3. **Orchestrate**: an `ExamOrchestrator` is constructed and `.start()`ed
   — either bootstraps a fresh session (`INIT_DEVICE_CHECK` auto-pass,
   since there is no device-check/ID-capture UI yet) or re-anchors
   guardrails and resumes any live Part 2 watchdog against its unchanged
   Redis deadline.
4. **Three concurrent coroutines** run for the connection's lifetime:
   - `writer()` — drains an `asyncio.Queue` outbox to the client (JSON
     text frames for control/caption/status, raw binary for audio).
   - `pump_client_to_gemini()` — dispatches `activity_start`/
     `activity_end`/`cue_card_ack`/`client_ping`/`rtt_report` control
     messages and forwards binary PCM frames to the bridge while a turn is
     active. `activity_end` captures the turn's phase via
     `orchestrator.on_turn_flush_started()` **before** calling
     `orchestrator.on_activity_end()` — ordering that matters because the
     latter can itself advance the phase (see migration `0005`'s
     docstring for the race this avoids).
   - `pump_gemini_to_client()` — relays `AudioDelta`/`TranscriptDelta`/
     `TurnComplete`/`Interrupted`/`GoAway` events, records the Spec 01
     §4.4 latency histograms on the first audio delta of a turn, and
     persists any `SessionResumptionUpdate`.
5. **Media persistence** runs off the critical path
   (`_flush_turn_in_background`) so an S3 round-trip never delays audio
   reaching the client — but the orchestrator's `FINALIZING` phase watches
   `_pending_flush_turn_ids` with a bounded watchdog before advancing to
   `COMPLETE`, so exam completion can never race an in-flight write.
6. **On `COMPLETE`**: `session.status` is set to `COMPLETED` and
   `enqueue_grading()` fires the Celery pipeline root task — wrapped in a
   broad `except Exception: logger.exception(...)` so a broker outage can
   never break exam completion on the live path (it's retryable
   out-of-band).

### Observability (`app/services/observability.py`)

Instruments exactly the hops that are actually observable server-side
(Spec 01 §4.4's total budget spans hops the browser and network also
contribute to, which this process cannot see):

| Metric | What it measures |
|---|---|
| `gateway_to_gemini_send_ms` | Hop 3 proxy: `activity_end` received → `bridge.send_activity_end()` returns |
| `gemini_response_ms` | Hops 4+5 **combined** (Gemini's own generation time and the Gemini→gateway network hop are not separable server-side — documented, not faked) |
| `gateway_relay_enqueue_ms` | Hop 6 proxy: first `AudioDelta` received → queued onto the outbox |
| `ptt_release_to_first_audio_ms` | Total server-observable leg (t0→t3), replacing the old log-line-only calculation |
| `client_gateway_rtt_ms` | Hop 2 proxy — **client-reported** via a `client_ping`/`pong`/`rtt_report` exchange, since the server cannot measure round-trip time unilaterally |

Exported via `prometheus_client`'s ASGI app at `GET /metrics`; spans go to
OTLP if `OTEL_EXPORTER_OTLP_ENDPOINT` is set, otherwise nowhere (no
collector required for dev/CI). See
[`infra/README.md`](../../infra/README.md) for the bundled
Prometheus/Grafana dev stack and the pre-provisioned latency-budget
dashboard.

## Configuration (`app/config.py`)

All settings are a single `pydantic-settings` `Settings` object, loaded
from environment variables (or a `.env` file — see `.env.example`) with no
`env_prefix`. Every field has a safe, insecure-by-design local-dev
default; **production deployment must override every secret-shaped
field.**

| Setting | Default | Notes |
|---|---|---|
| `database_url` | `postgresql+asyncpg://ielts:ielts@localhost:5432/ielts_speaking` | asyncpg driver; `apps/worker` reads the same var and swaps to psycopg2 |
| `redis_url` | `redis://localhost:6379/0` | Session snapshot cache + Part 2 timer deadlines |
| `jwt_secret` / `jwt_algorithm` / `jwt_access_token_ttl_minutes` | insecure dev default / `HS256` / `60` | **Must be rotated for any non-dev deployment** |
| `resume_token_ttl_hours` | `24` | Declared but not yet wired into a TTL check (resume-token rotation is part of Phase 4's broader reconnect story) |
| `cors_origins` | `["http://localhost:3000"]` | |
| `s3_endpoint_url` / `s3_access_key` / `s3_secret_key` / `s3_bucket` / `s3_region` | MinIO dev defaults | |
| `raw_video_retention_days` | `90` | Enforced as a real S3 bucket lifecycle rule via `configure_bucket_lifecycle()`, not just documentation (Spec 01 §7, Phase 8) |
| `gemini_api_key` | `""` | **Never reaches the client** — used exclusively server-side to build the outbound Gemini WS URL (enforced by `tests/integration/test_no_credential_leakage.py`) |
| `live_model_id` | `models/gemini-2.0-flash-live-001` | Config-driven per Spec 01 §4.1 — a new Live model version is a deploy-time swap, not a client release |
| `gemini_live_ws_url` | real Google endpoint | Integration tests override this to point at `tests/support/fake_gemini_live_server.py` so CI never dials out |
| `gemini_output_sample_rate_hz` | `24000` | |
| `prompt_templates_dir` | `packages/prompt-templates` (repo-root-relative) | |
| `part2_prep_seconds` / `part2_long_turn_seconds` / `part2_long_turn_warn_at_seconds` | `60` / `120` / `115` | Hard, non-negotiable exam-format durations in production; tests override to sub-second values |
| `intro_turns` / `part1_topic_turns` / `part2_roundoff_turns` / `part3_discussion_turns` / `reanchor_every_n_turns` | `1` / `4` / `2` / `6` / `6` | Soft-phase-exit turn budgets (approximating Spec 02 §1's "soft target" exits — real conversational-arc detection is out of scope) |
| `finalizing_watchdog_seconds` | `60` | Bounds how long `FINALIZING` waits on in-flight media flushes before force-advancing |
| `celery_broker_url` | `amqp://ielts:ielts@localhost:5672//` | Producer-only — see `grading_trigger.py` |
| `internal_debug_token` | insecure dev default | Gates `/internal/*`; **must be rotated for any non-dev deployment** |

## Dependencies

Declared in `pyproject.toml`: `fastapi`, `uvicorn[standard]`,
`sqlalchemy[asyncio]` + `asyncpg`, `alembic`, `pydantic[email]` +
`pydantic-settings`, `redis`, `python-jose[cryptography]` (JWT),
`python-multipart`, `boto3`, `websockets` (the Gemini bridge — see ADR
0001), `celery` (producer-only client), and the OpenTelemetry stack
(`opentelemetry-api`/`sdk`/`exporter-prometheus`/`exporter-otlp-proto-grpc`,
Phase 8). This app also installs `packages/exam-fsm` as a sibling editable
install (`pip install -e packages/exam-fsm`), not a PEP 508 dependency —
same pattern CI uses.

`dev` extra: `pytest`, `pytest-asyncio`, `httpx` (also the `TestClient`
transport), `ruff`, `psycopg2-binary` (Alembic's synchronous driver only).

## Running locally

```bash
# 1. Start shared infra (Postgres, Redis, RabbitMQ, MinIO — see infra/README.md)
docker-compose -f infra/docker/docker-compose.dev.yml up -d

# 2. Install (editable, with dev extras)
pip install -e packages/exam-fsm
pip install -e "apps/api-gateway[dev]"

# 3. Apply migrations (owns the schema; see migrations/README.md)
alembic -c migrations/alembic.ini upgrade head

# 4. Run the gateway
cd apps/api-gateway && uvicorn app.main:app --reload
```

Set `GEMINI_API_KEY` (and, for a real end-to-end run, deploy the licensed
rubric asset — see `packages/grading-rubric-assets/README.md`, consumed
downstream by `apps/worker`, not this app) via `.env` or the shell
environment; every other setting has a working local default.

## Testing

| Suite | Command | What it needs |
|---|---|---|
| Unit (`app/tests/`) | `pytest apps/api-gateway/app/tests` | Nothing — `TestClient(app)` only |
| Integration (repo-root `tests/integration/`) | `pytest tests/integration` | Real Postgres + MinIO (`docker-compose up -d`); Gemini is **always** the fixture-replay fake server (`tests/support/fake_gemini_live_server.py`), never the real vendor |
| Load harness (repo-root `tests/load/`) | `pytest tests/load` | Spawns a real `uvicorn` subprocess against the fake Gemini server; low-concurrency CI smoke only — see `tests/README.md` for the real-load invocation |

Notable integration tests that exercise this app end-to-end:
`test_auth_session_flow.py`, `test_video_presigned_upload.py`,
`test_gemini_bridge_replay.py` (protocol-level, no FastAPI),
`test_exam_room_gemini_relay.py` (one real PTT turn through the live WS
route), `test_full_exam_session_flow.py` (the entire Spec 02 §1 phase
sequence, INIT→COMPLETE, against the fixture-replay Gemini server),
`test_no_credential_leakage.py` (asserts no vendor secret ever appears in
a client-facing WS frame or `/metrics`), `test_observability_metrics.py`,
`test_internal_debug_endpoint.py`, `test_grading_trigger.py`.

CI (`.github/workflows/ci.yml`) runs the unit suite, the integration suite
against real service containers, and the load-harness smoke test on every
PR — `ruff check apps/api-gateway ...` gates first.

## Cross-references

- Media/audio contract, latency budget, resiliency model: `docs/SPEC_01_SYSTEM_ARCHITECTURE.md`
- Exam FSM phase table, Part 2 timer semantics, directive injection: `docs/SPEC_02_IELTS_FLOW_STATE_MACHINE.md`
- Phased build plan this app was built against: `docs/SPEC_04_REPOSITORY_AND_BUILD_PLAN.md`
- Pure FSM logic this app wraps: [`packages/exam-fsm/README.md`](../../packages/exam-fsm/README.md)
- Versioned persona/directive text this app injects: [`packages/prompt-templates/README.md`](../../packages/prompt-templates/README.md)
- Downstream async grading consumer: [`apps/worker/README.md`](../worker/README.md)
