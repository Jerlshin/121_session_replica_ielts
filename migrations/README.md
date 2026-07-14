# `migrations`

Alembic migration history for the shared Postgres schema described in
`docs/SPEC_01_SYSTEM_ARCHITECTURE.md` §6. This directory **orchestrates**
schema changes; it does not **own** the schema definition — the
authoritative SQLAlchemy models live in `apps/api-gateway/app/models/`,
and `migrations/env.py` imports them directly so every migration is
generated against (and reviewable against) that single source of truth.
`apps/worker` never generates migrations; it maintains its own read-only
sync mirror of a subset of these tables (see
[`apps/worker/README.md`](../apps/worker/README.md#shared-schema-ownership)).

## Why migrations live outside `apps/api-gateway`

Both `apps/api-gateway` (async, asyncpg) and `apps/worker` (sync,
psycopg2) read and write the same physical Postgres database — the schema
is a genuinely shared, cross-app artifact, not something either
individual app should own the migration tooling for. Keeping `migrations/`
at the repo root, orchestrating against `apps/api-gateway`'s models via an
explicit `sys.path` insert (`migrations/env.py`), keeps that shared
ownership visible in the directory structure itself rather than implying
either app is "in charge" of the other's schema.

## Directory structure

```
migrations/
├── alembic.ini            Alembic runtime config (logging, script location)
├── env.py                 Migration environment — imports app.models, resolves DATABASE_URL
├── script.py.mako         Template for new migration files
└── versions/
    ├── 0001_initial_schema.py
    ├── 0002_media_segments.py
    ├── 0003_exam_content.py
    ├── 0004_grading_pipeline.py
    ├── 0005_audio_segment_phase.py
    ├── 0006_feature_vectors.py
    └── 0007_band_score_reports.py
```

## `env.py` — the sync/async driver bridge

Alembic runs synchronously; the application stack is split between
asyncpg (`apps/api-gateway`) and psycopg2 (`apps/worker`). `env.py`
resolves `DATABASE_URL` from the environment (falling back to the same
local-dev default every app's `Settings` uses) and unconditionally
rewrites the scheme:

```python
url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
```

so a single `DATABASE_URL` env var works for the live app, the worker,
and migrations alike — nobody has to maintain a second, migration-specific
connection string. `target_metadata = Base.metadata` is imported straight
from `apps/api-gateway/app/db.py`/`app/models/__init__.py`
(`sys.path.insert(0, apps/api-gateway)` at the top of `env.py`), which is
what lets `alembic revision --autogenerate` diff against the real ORM
model state rather than a hand-copied schema description.

## Migration history

| Revision | Adds | Notes |
|---|---|---|
| **`0001`** | `candidates`, `exam_sessions`, `exam_session_events` | Phase 0 foundational schema — identity, durable session row, and the append-only event-sourced FSM log (`UNIQUE(session_id, seq)`) that everything else in the system is folded from |
| **`0002`** | `audio_segments`, `video_segments` | Phase 1's media spine — raw per-turn audio pointers (`UNIQUE(session_id, turn_id, seq)`, idempotent against retried flushes) and the proctoring video pointer table, kept structurally separate from the start |
| **`0003`** | `topic_sets`, `cue_cards`, plus `candidates.previous_topic_sets` and `exam_sessions.cue_card_id` | Phase 3's content banks — Part 1 topic sets (`CHECK (slot IN ('A','B','C'))`) and Part 2 cue cards. **Seeds fixed, reproducible content** at fixed UUIDs (`_TOPIC_A1`, `_CUE_1`, etc. — hardcoded in the migration, not randomly generated) so `alembic upgrade head` against a fresh database is deterministic, matching Spec 04 §3's anti-regression spirit that content, like schema, should be a reviewable diff |
| **`0004`** | `grading_jobs`, `transcripts` | Phase 5 — the idempotency/status table every Celery pipeline task upserts into (`UNIQUE(session_id, task_name)`, never appended, enabling a targeted solo re-run of just a failed stage) and the word-level canonical transcript table, the sole source of truth for grading |
| **`0005`** | `audio_segments.exam_phase` | Phase 6 — captures the raw `ExamPhase` a turn was spoken in, recorded synchronously at turn-flush time rather than reconstructed later from `exam_session_events` timestamps, which is racy for exactly the last turn of every phase (see the migration's own docstring and `apps/api-gateway/app/services/exam_orchestrator.py::on_turn_flush_started` for the full race description) |
| **`0006`** | `feature_vectors` | Phase 6 — one row per `(session_id, criterion, phase)`, `UNIQUE` and upserted, storing each of the four criteria's computed metric JSON + provenance per phase and as a session aggregate |
| **`0007`** | `band_score_reports` | Phase 7 — one row per `session_id`, upserted, storing the complete audit trail (`judge_input`, `judge_pass_1`, `judge_pass_2`, `reconciliation`, `flag_for_human_review`) so any score is fully reconstructable against the exact evidence the model saw |

Every table added from `0004` onward follows the same **upsert-not-append
idempotency contract**: a Celery task re-run overwrites its own prior row
(keyed on a stable natural key — `(session_id, task_name)`,
`(session_id, criterion, phase)`, or `session_id` alone) rather than
accumulating duplicate rows, which is what makes Spec 03 §2.4's "a failed
stage supports a targeted solo re-run" actually safe to do in production.

No migration since `0007` has been added — Phase 8 (hardening/load) and
Phase 9 (calibration) both introduced no new tables (Phase 8's data-
retention sweep operates on existing `audio_segments` rows and S3 objects
only; Phase 9's calibration corpus is a JSON test fixture, not a database
table).

## Running migrations

```bash
# Apply every migration up to head
alembic -c migrations/alembic.ini upgrade head

# Check the currently-applied revision
alembic -c migrations/alembic.ini current

# Roll back one revision
alembic -c migrations/alembic.ini downgrade -1

# Generate a new migration from a model change in apps/api-gateway/app/models/
alembic -c migrations/alembic.ini revision --autogenerate -m "short_description"
```

`DATABASE_URL` must point at a running Postgres instance — see
[`infra/README.md`](../infra/README.md) for the bundled `docker-compose`
dev stack (`postgres:16` on `localhost:5432`, matching every app's
default). CI runs `alembic -c migrations/alembic.ini upgrade head` as a
dedicated step (`.github/workflows/ci.yml`) against a fresh service
container on every PR, so a migration that doesn't apply cleanly to an
empty database fails the build immediately, before any test runs.

## Conventions for adding a new migration

1. Change the model in `apps/api-gateway/app/models/` first — this is
   still the single source of truth Alembic diffs against.
2. Run `--autogenerate` and **read the generated diff carefully** —
   autogenerate reliably catches column/table additions but does not
   reliably catch constraint renames, `CHECK` constraints, or data
   migrations; `0003`'s seed-data `INSERT`s were hand-written, not
   autogenerated.
3. Give the revision file a descriptive docstring explaining the *why*,
   not just the *what* — every existing migration in this repo does this
   (see `0005`'s docstring for the canonical example: it explains the
   race condition the column exists to avoid, not just "adds a column").
4. If the new table stores data a Celery task writes, follow the existing
   upsert-not-append pattern (a `UniqueConstraint` on the natural key,
   written via `sqlalchemy.dialects.postgresql.insert(...).on_conflict_do_update(...)`
   — see `apps/worker/job_status.py`, `feature_vectors.py`, or
   `band_score_reports.py` for the established helper pattern) rather than
   inventing a new idempotency strategy per table.

## Cross-references

- Full core data model summary table: `docs/SPEC_01_SYSTEM_ARCHITECTURE.md` §6
- Event-sourced resiliency model `exam_session_events` implements: `docs/SPEC_01_SYSTEM_ARCHITECTURE.md` §5
- Async-side canonical model definitions: [`apps/api-gateway/README.md`](../apps/api-gateway/README.md#appmodels--sqlalchemy-schema-async-side)
- Sync-side mirror and the idempotency helpers referenced above: [`apps/worker/README.md`](../apps/worker/README.md)
