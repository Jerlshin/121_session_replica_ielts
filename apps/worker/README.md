# `apps/worker`

The asynchronous, four-criteria IELTS Speaking grading engine. A Celery
application that turns a completed exam session's raw evidence (candidate
audio, event log) into a defensible, evidence-grounded band score report —
implementing
[`docs/SPEC_03_ASYNC_GRADING_ENGINE.md`](../../docs/SPEC_03_ASYNC_GRADING_ENGINE.md)
in full: backline transcription, the four IELTS criteria's deterministic
feature extraction, the LLM Rubric Judge, self-consistency reconciliation,
and Phase 9's shadow-scoring calibration tooling.

This service **never** touches proctoring video (`VideoSegment` is not
even imported anywhere in this app — CLAUDE.md rule 3) and **never**
grades from a bare transcript impression — every LLM judgment is grounded
in pre-computed, auditable numeric features (CLAUDE.md rule 6, "Evidence
Before Judgment").

## Architectural role

```
apps/api-gateway ──enqueue(grading.grade_exam_session)──► RabbitMQ ──► apps/worker
                                                                            │
                                          reads/writes shared Postgres ────┤
                                          reads/writes shared S3/MinIO ────┤
                                          calls Deepgram/Azure/OpenAI ─────┘
                                          (real vendor code, gated behind
                                           API keys, never exercised in CI)
```

`apps/api-gateway/app/services/grading_trigger.py` enqueues exactly one
root task, `grading.grade_exam_session`, by name — this app is a pure
**consumer** of that queue; the gateway never imports this app's task
modules (which would pull spaCy/whisperx/torch into the live-serving
process).

## The pipeline DAG (`pipelines/grading_pipeline.py`)

```
finalize_media
      │
      ▼
transcribe_full_session
      │
      ▼
   ┌──┴───────────────┬───────────────────┬─────────────────────┐
   ▼                  ▼                   ▼                     ▼
compute_fluency   compute_lexical   compute_grammar   compute_pronunciation
   _metrics          _metrics          _metrics             _scores
   └──────────────────┴───────────────────┴─────────────────────┘
                              │ (chord)
                              ▼
                    synthesize_band_scores
                              │
                              ▼
                  sweep_expired_raw_audio
```

Built with Celery's `chain(...)` and `chord(group(...), callback)`
primitives. **Every stage uses an immutable signature (`.si()`, never
`.s()`)** — no stage receives a prior task's return value as an implicit
argument. Each task independently re-reads whatever it needs from
`grading_jobs`/`feature_vectors`/`transcripts` (via `job_status.py`'s
`load_result()`, `feature_vectors.py`, or a direct query). This is
deliberate: a chain-coupled signature would only work end-to-end through
the full chain, silently breaking Spec 03 §2.4's requirement that any
single failed stage supports a **targeted solo re-run**
(`transcribe_full_session.delay(session_id)` run alone, with no
`finalize_media` result freshly piped in, must still work).

**Known, deliberately undeferred limitation:** if one of the four
chord-group members permanently fails after exhausting its own retries,
Celery's chord callback may never fire (or fires with an incomplete
header, backend-dependent). `synthesize_band_scores` already tolerates an
individually-missing criterion gracefully (`feature_status: "missing"`)
— what it cannot do is fire at all if the chord itself never completes.
Recovery today is a manual `synthesize_band_scores.delay(session_id)`
re-run once the stuck member is fixed. Genuinely hardening this would mean
changing each Phase 6 task's own failure semantics, judged out of
proportion for the pipeline-wiring work itself.

## Directory structure

```
apps/worker/
├── celery_app.py                Celery app instance, task include/routing, retry policy
├── config.py                    Pydantic Settings — every vendor key, threshold, and path
├── db.py                        Sync SQLAlchemy engine (psycopg2) — Celery tasks run outside an event loop
├── models.py                    Sync ORM mirror of the shared schema subset this app touches
├── job_status.py                grading_jobs upsert helpers (idempotency contract, shared by every task)
├── feature_vectors.py           feature_vectors upsert helper
├── band_score_reports.py        band_score_reports upsert helper
├── nlp_common.py                Shared NLP plumbing: phase-bucketed transcript loading, cached spaCy pipeline, utterance segmentation, syllable/frequency lookups
├── rubric_assets.py             Loads the licensed band-descriptor asset at judge-call time
├── reconciliation.py            self_consistency_reconciliation — pure function, Spec 03 §5.6
├── storage.py                   S3/MinIO client factory + bucket lifecycle configuration
├── calibration_metrics.py       Phase 9: MAE/RMSE/agreement/Pearson/Spearman/QWK — pure statistics
├── calibration_corpus.py        Phase 9: BenchmarkCase model, corpus loader, CorpusScriptedScoringLLM
├── calibration_report.py        Phase 9: CalibrationConfig, run_calibration(), fallback-gating dry run
├── tasks/
│   ├── media.py                 finalize_media, sweep_expired_raw_audio
│   ├── asr.py                   transcribe_full_session
│   ├── scoring.py                synthesize_band_scores
│   ├── calibration.py            Phase 9 CLI entry point (NOT a Celery task)
│   ├── pronunciation.py          compute_pronunciation_scores
│   └── nlp/
│       ├── fluency.py            compute_fluency_metrics
│       ├── lexical.py            compute_lexical_metrics
│       ├── grammar.py            compute_grammar_metrics
│       └── lexicons.py           Curated word lists (filled pauses, discourse markers, collocations)
├── providers/
│   ├── transcription.py          DeepgramTranscriptionProvider, WhisperXTranscriptionProvider
│   ├── grammar_check.py          LanguageToolProvider
│   ├── pronunciation.py          AzurePronunciationProvider, GOPFallbackProvider, librosa prosody proxy
│   └── scoring_llm.py            OpenAIScoringLLM, JudgeInput/JudgeOutput schemas, prompt builder
├── pipelines/
│   └── grading_pipeline.py       The DAG assembly described above
└── tests/                        Unit tests (no real Postgres/broker/vendor calls)
```

## Shared schema ownership

`apps/api-gateway/app/models/` is the **canonical** owner of the shared
Postgres schema; Alembic migrations are generated from it. `models.py` in
this app is a **separate, sync-mapped (psycopg2) mirror** of only the
tables/columns this worker actually reads or writes
(`ExamSession`/`Candidate` narrowed to the couple of columns Phase 7's
judge input needs, `AudioSegment`, `GradingJob`, `Transcript`,
`FeatureVector`, `BandScoreReport`) — deliberately never `VideoSegment`.
The two model sets are kept in sync by hand, not by a shared ORM package,
because Celery tasks run outside an asyncio event loop and therefore
cannot share the gateway's async engine; `db.py` normalizes one shared
`DATABASE_URL` env var (`+asyncpg` → `+psycopg2`) so both apps read the
same connection string without a second env var to keep in sync.
`packages/shared-schemas` was scaffolded for exactly this kind of
duplication but was never actually populated — see
[`packages/shared-schemas/README.md`](../../packages/shared-schemas/README.md)
for why, and what unifying this would require.

## Stage-by-stage reference

### Media & transcription (Phase 5)

- **`finalize_media`** (`tasks/media.py`) — stitches every per-turn
  candidate WAV (`raw-audio/{session}/segments/*.wav`) into one canonical
  FLAC (`raw-audio/{session}/canonical.flac`, Spec 01 §7's long-term
  scoring evidence of record), recording each turn's offset within the
  stitched file for later back-mapping. Only ever reads `AudioSegment`.
- **`transcribe_full_session`** (`tasks/asr.py`) — the authoritative
  batch re-transcription of `canonical.flac`; materially more accurate
  than the live Gemini caption lane (Spec 01 §4.3) and the **sole source
  of truth for grading**. Delete-then-insert into `transcripts` per run
  (a shorter re-run's word count can legitimately differ from a prior
  run's — per-word upsert can't guarantee idempotency for that shape).
- **`sweep_expired_raw_audio`** (`tasks/media.py`, Phase 8) — the final
  DAG step, firing only once `synthesize_band_scores` has succeeded.
  Deletes the now-redundant per-turn raw segments (Spec 01 §7: "session
  lifetime + grading buffer," not long-term) while keeping
  `canonical.flac` and the `audio_segments` DB rows as an audit trail —
  only the S3 bytes (the sensitive payload) are removed.

**Providers** (`providers/transcription.py`): `DeepgramTranscriptionProvider`
(primary, real batch call) → `WhisperXTranscriptionProvider` (self-hosted
fallback, `apps/worker[whisperx]` optional extra) via
`transcribe_with_fallback()`'s confidence-gated chain
(`settings.transcription_confidence_floor`, default `0.7`).

### Feature extraction (Phase 6) — all four criteria, `tasks/nlp/*` + `tasks/pronunciation.py`

Every `compute_*` task follows the identical shape: load phase-bucketed
transcript words (`nlp_common.load_words_by_phase`) → compute metrics per
phase (`part1`/`part2`/`part3`) and as a `session` aggregate → upsert each
into `feature_vectors` with a `provenance` block naming the metric source.

| Task | Criterion | Method | Notable metrics |
|---|---|---|---|
| `compute_fluency_metrics` | Fluency & Coherence | 100% rule-based over word timestamps, no vendor | Speech/articulation rate, phonation time ratio, mean length of run, micro/macro pause rate, clause-boundary vs. mid-clause pause placement, filled-pause/self-repair rate, discourse-marker usage |
| `compute_lexical_metrics` | Lexical Resource | spaCy lemmatization + `wordfreq` Zipf frequency (a documented proxy for a licensed graded lexicon) + LanguageTool | MTLD, MATTR, CEFR-proxy distribution, off-top-5000 rarity ratio, collocation/idiom match count, lexical-appropriacy error rate |
| `compute_grammar_metrics` | Grammatical Range & Accuracy | spaCy dependency parse (T-unit segmentation) + LanguageTool | Mean length of T-unit, clauses/T-unit, dependent-clause ratio, complex nominals/clause, coordination index, structural range (tense/passive/conditional/relative-clause/modal diversity), error-free-clause ratio, error-type taxonomy |
| `compute_pronunciation_scores` | Pronunciation | Azure AI Speech (primary) / self-hosted wav2vec2-CTC GOP (fallback), plus a real `librosa`-computed prosody proxy that runs regardless of vendor path | Accuracy/fluency/completeness/prosody per turn, pitch range, stress-timing regularity, per-segment `source` provenance |

Shared plumbing lives in `nlp_common.py` (a process-wide cached
`en_core_web_sm` spaCy pipeline — not the `en_core_web_trf` transformer
model named illustratively in Spec 01 §3, a documented deviation since no
Phase 6 metric needs transformer-grade parsing) and `tasks/nlp/lexicons.py`
(curated filled-pause/discourse-marker/self-repair/collocation word
lists).

**Grammar/lexical accuracy provider** (`providers/grammar_check.py`):
`LanguageToolProvider`, real but lazy (`apps/worker[languagetool]`
optional extra — wraps a JRE + ~200MB server jar). **Pronunciation
providers** (`providers/pronunciation.py`): `AzurePronunciationProvider`
(real Azure AI Speech Pronunciation Assessment call, unscripted mode — the
backline ASR transcript stands in as the reference text) and
`GOPFallbackProvider` (real Witt & Young-style Goodness-of-Pronunciation
formula over `torchaudio.functional.forced_align`, `apps/worker[gop]`
optional extra), gated by `assess_with_fallback()`'s confidence-and-SNR
chain (`settings.pronunciation_confidence_floor`, default `0.7`).

### The LLM Rubric Judge (Phase 7) — `providers/scoring_llm.py`, `tasks/scoring.py`

`synthesize_band_scores` assembles Phase 6's feature vectors plus
per-phase transcript text into a `JudgeInput` (never a bare transcript —
CLAUDE.md rule 6), runs **two independent `ScoringLLM.score()` passes**,
and reconciles them.

- **`ScoringLLM` protocol**: `score(judge_input: JudgeInput) -> JudgeOutput`
  plus a `source_name` — model-agnostic by design (Spec 03 §5.2).
- **`OpenAIScoringLLM`**: the default production implementation, OpenAI in
  structured-output mode via `client.responses.parse()`, gated behind
  `OPENAI_API_KEY`, never exercised in CI. `build_judge_system_prompt()`
  renders the fixed `JUDGE_SYSTEM_PROMPT` template against the licensed
  rubric text and optionally appends a `<<CALIBRATION_DIRECTIVE>>` block
  (Phase 9's prompt-tuning knob — unset in every non-calibration caller,
  so production behavior is unchanged by its existence).
- **`JudgeInput`/`JudgeOutput`** (Pydantic): exactly the schemas in Spec
  03 §5.3/§5.4 — `CriterionScore.justification` must name specific
  feature values; the model is explicitly instructed never to re-derive a
  fluency/grammar impression from raw transcript text.
- **`rubric_assets.py`**: loads `band_descriptors_v{n}.json` from
  `settings.rubric_assets_dir` **at judge-call time**, never hardcoded —
  see [`packages/grading-rubric-assets/README.md`](../../packages/grading-rubric-assets/README.md).
  Raises `RubricAssetError` loudly if the licensed asset hasn't been
  deployed, rather than silently degrading.
- **`reconciliation.py::self_consistency_reconciliation`** (pure
  function): per-criterion band comparison between the two passes. A
  criterion missing from either pass, or disagreeing by more than
  `settings.self_consistency_band_disagreement_threshold` (default
  `1.0`), is excluded from the averaged reference score and routes the
  session to `flag_for_human_review` — never silently split-the-difference
  averaged. The full audit trail (`JudgeInput`, both raw `JudgeOutput`s,
  and the reconciliation decision) is **always** persisted to
  `band_score_reports` regardless of outcome, via `band_score_reports.py`.

### Phase 9 — Shadow-scoring & calibration

Batch-runs a benchmark corpus of sessions with certified human gold band
scores through the exact same judge/reconciliation code path production
uses, and reports rater-vs-judge statistical agreement.

- **`calibration_metrics.py`** (pure, stdlib-only — no scipy dependency):
  MAE, RMSE, exact/0.5-band/1.0-band agreement rates, Pearson and Spearman
  correlation (average-rank tie handling), and quadratic weighted kappa
  (Cohen's formula over the 19 possible 0.5-increment band classes;
  returns `None` rather than dividing by zero on degenerate/single-class
  inputs).
- **`calibration_corpus.py`**: `BenchmarkCase` (Pydantic — phases,
  feature vectors, `human_scores`, two scripted `simulated_pass_1`/
  `simulated_pass_2` judge outputs, optional recorded ASR/pronunciation
  confidence), `load_benchmark_corpus()`, and `CorpusScriptedScoringLLM` —
  a deterministic, multi-session-aware `ScoringLLM` that returns each
  case's `simulated_pass_1` on the first call for that session and
  `simulated_pass_2` on the second, so `run_calibration()` drives it
  through the identical two-pass shape production does. The bundled
  8-case demo corpus lives at
  [`tests/fixtures/calibration_benchmark/benchmark_corpus_v1.json`](../../tests/fixtures/calibration_benchmark/benchmark_corpus_v1.json)
  — synthetic, hand-authored, committed to source control (unlike the
  licensed rubric text, it carries no licensing/PII concern).
- **`calibration_report.py`**: `CalibrationConfig` (the four tunable
  knobs — reconciliation threshold, judge prompt directive, and the two
  confidence floors), `run_calibration()` (reuses
  `reconciliation.self_consistency_reconciliation` **unmodified**, so
  tuning the threshold exercises the same code path production runs), and
  `fallback_gating_dry_run()` (reports which corpus cases' *recorded*
  confidence values would trigger Phase 5/6's real fallback gating at a
  given floor — informational only, since the corpus has no raw audio to
  re-run extraction on).
- **`tasks/calibration.py`** — the CLI. **Not a Celery task** (no
  `@app.task`, not registered in `celery_app.py`); an operator-invoked
  offline tool. Defaults to a zero-config dry run against the bundled
  corpus (`CorpusScriptedScoringLLM` — no API key, no network); `--live`
  switches to the real `OpenAIScoringLLM`.

  ```bash
  cd apps/worker
  python -m tasks.calibration                                    # dry run, bundled corpus
  python -m tasks.calibration --reconciliation-threshold 1.5      # sweep the tuning knob
  python -m tasks.calibration --json-out /tmp/report.json         # machine-readable output
  python -m tasks.calibration --live --prompt-directive "Weight grammatical accuracy more heavily"
  ```

## Celery configuration (`celery_app.py`)

```python
app = Celery("ielts_grading_engine", broker=broker_url, backend=result_backend)
```

- **Queues** (Spec 03 §2.3 — I/O-heavy stages get dedicated pools sized
  independently of lightweight ones): `media` (`finalize_media`,
  `sweep_expired_raw_audio`), `asr` (`transcribe_full_session`), `nlp`
  (all four `tasks.nlp.*` + `tasks.pronunciation.*`), `pronunciation`,
  `scoring` (`synthesize_band_scores`).
- **Reliability**: `task_acks_late=True` + `task_reject_on_worker_lost=True`
  — a crashed worker mid-task must not lose the job — plus
  `task_retry_backoff=True`.
- `tasks.calibration` is **not** in the `include` list — it has no
  `@app.task` decorator and is never dispatched via the broker.

## Configuration (`config.py`)

| Setting | Default | Notes |
|---|---|---|
| `database_url` | shared with `apps/api-gateway`; normalized to `+psycopg2` in `db.py` | |
| `s3_endpoint_url` / `s3_access_key` / `s3_secret_key` / `s3_bucket` / `s3_region` | MinIO dev defaults | |
| `raw_video_retention_days` | `90` | Same bucket-lifecycle enforcement as the gateway (Phase 8) |
| `deepgram_api_key` / `deepgram_api_url` / `deepgram_model` / `deepgram_timeout_s` | empty / Deepgram's endpoint / `nova-2` / `120.0` | Empty key fails loudly, never silently no-ops |
| `transcription_confidence_floor` | `0.7` | ASR fallback gate |
| `whisperx_model_name` / `whisperx_device` / `whisperx_language_code` | `large-v3` / `cpu` / `en` | |
| `azure_speech_key` / `azure_speech_region` / `azure_speech_timeout_s` | empty / `eastus` / `60.0` | |
| `pronunciation_confidence_floor` | `0.7` | Pronunciation fallback gate |
| `gop_model_name` | `facebook/wav2vec2-lv-60-espeak-cv-ft` | |
| `openai_api_key` | `""` | Required for `OpenAIScoringLLM`; never required for the dry-run calibration path |
| `scoring_llm_model` | `gpt-5.1` | |
| `rubric_assets_dir` | `packages/grading-rubric-assets` (repo-root-relative) | Deliberately never committed — see that package's README |
| `self_consistency_band_disagreement_threshold` | `1.0` | Spec 03 §5.6; the Phase 9 CLI's `--reconciliation-threshold` overrides this per-run without touching production config |

## Dependencies & optional extras

Core (`pyproject.toml`): `celery[redis]`, `redis`, `sqlalchemy` +
`psycopg2-binary`, `pydantic`/`pydantic-settings`, `boto3`, `soundfile`,
`numpy`, `httpx`, `spacy` + `en_core_web_sm`, `wordfreq`, `librosa`,
`pronouncing`, `openai` (a normal dependency — a thin `httpx`-based
client, unlike the heavy extras below).

Opt-in extras, each raising a clear, actionable error if invoked without
being installed (never silently no-op-ing):

| Extra | Installs | Used by |
|---|---|---|
| `whisperx` | `whisperx` | `WhisperXTranscriptionProvider` fallback |
| `languagetool` | `language-tool-python` (wraps a JRE + ~200MB server jar) | `LanguageToolProvider` |
| `gop` | `transformers`, `torch`, `torchaudio` | `GOPFallbackProvider` |

None of these three real-vendor code paths — nor Deepgram, Azure, or
OpenAI — are exercised in CI; every test substitutes a deterministic
fixture/fake provider injected through the same interface the real
implementation satisfies.

## Running locally

```bash
docker-compose -f infra/docker/docker-compose.dev.yml up -d
pip install -e "apps/worker[dev]"
alembic -c migrations/alembic.ini upgrade head    # shared schema, owned by apps/api-gateway
cd apps/worker && celery -A celery_app worker --loglevel=info
```

Every cross-module import in this app (`from config import settings`,
`from band_score_reports import ...`, etc.) relies on `apps/worker` being
on `sys.path` — true automatically when `cwd` is `apps/worker` (Celery's
`-A` resolution inserts `cwd`), and explicitly via `sys.path.insert(0, ...)`
in every test file.

## Testing

| Suite | Command | What it needs |
|---|---|---|
| Unit (`apps/worker/tests/`) | `pytest apps/worker/tests` | Nothing — pure functions and fixture doubles only |
| Integration (repo-root `tests/integration/`) | `pytest tests/integration` | Real Postgres + MinIO; every vendor call substitutes a fixture provider |

Key unit tests: `test_reconciliation.py` (agreement/disagreement/missing-
criterion cases), `test_grading_pipeline.py` (asserts the exact DAG shape
— chain → chord → sweep step, every signature immutable), `test_celery_app.py`,
`test_media_retention.py` (fake-S3 deletion-logic test), `test_calibration_metrics.py`,
`test_calibration_corpus.py`, `test_calibration_report.py`,
`test_scoring_llm_prompt_suffix.py`.

Key integration tests: `test_finalize_media.py`, `test_transcribe_full_session.py`,
`test_feature_extraction_pipeline.py` (all four `compute_*` tasks against a
real Phase-5 transcript), `test_fluency_metrics.py`/`test_lexical_metrics.py`/
`test_grammar_metrics.py`/`test_pronunciation_scores.py` (golden-file tests
against `tests/fixtures/reference_audio/`, pinning metric *ranges* not
exact values — Spec 04 §3), `test_synthesize_band_scores.py` (disagreement
injection → `flag_for_human_review`), `test_media_retention_sweep.py`,
`test_calibration_pilot.py` (the full 8-case benchmark corpus end-to-end).

## Cross-references

- Full pipeline DAG, per-criterion metric formulations, judge schemas, self-consistency semantics: `docs/SPEC_03_ASYNC_GRADING_ENGINE.md`
- Object storage retention topology, security/compliance notes: `docs/SPEC_01_SYSTEM_ARCHITECTURE.md` §7–§8
- Phased build plan (Phases 5–9 built this app): `docs/SPEC_04_REPOSITORY_AND_BUILD_PLAN.md`
- Licensed rubric asset contract: [`packages/grading-rubric-assets/README.md`](../../packages/grading-rubric-assets/README.md)
- Upstream producer of the root task: [`apps/api-gateway/README.md`](../api-gateway/README.md)
- Migration history for every table this app reads/writes: [`migrations/README.md`](../../migrations/README.md)
