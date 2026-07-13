"""grade_exam_session — the Spec 03 §2.2 DAG's chain root, enqueued once
per closed session (Spec 03 §2.1): finalize_media -> transcribe_full_session
-> a chord fanning the four Phase 6 feature-extraction tasks (E1-E4) out in
parallel into synthesize_band_scores (Spec 03 §5) as the callback.

Every stage uses an *immutable* signature (`.si()`, not `.s()`) — none
receives a prior task's return value as an implicit argument. Each
independently re-reads whatever it needs from `grading_jobs`/
`feature_vectors` (`job_status.load_result`, `synthesize_band_scores`'s own
`_load_feature_vectors`). This is deliberate: a chain-coupled signature
(`.s()`) would only work end-to-end through the full chain, silently
breaking Spec 03 §2.4's "targeted re-run of just the failed sub-task" —
e.g. `transcribe_full_session.delay(session_id)` run alone, with no
`finalize_media` result freshly piped in.

Known limitation, deliberately not solved here: if one of the four chord-
group members permanently fails after exhausting its own retries, Celery's
chord callback may never fire (or fires with an `chord_error`/incomplete
header, backend-dependent) — genuinely hardening that would mean changing
each Phase 6 task's own failure semantics, out of proportion for this
pipeline-wiring change. `synthesize_band_scores` already tolerates a
individually-missing criterion gracefully (`feature_status: "missing"`,
Spec 03 §5.3) — what it can't do is fire at all if the chord itself never
completes; recovering from that today means a manual
`synthesize_band_scores.delay(session_id)` re-run once the stuck group
member is fixed and re-run.
"""
from celery import chain, chord, group

from celery_app import app
from tasks.asr import transcribe_full_session
from tasks.media import finalize_media
from tasks.nlp.fluency import compute_fluency_metrics
from tasks.nlp.grammar import compute_grammar_metrics
from tasks.nlp.lexical import compute_lexical_metrics
from tasks.pronunciation import compute_pronunciation_scores
from tasks.scoring import synthesize_band_scores


@app.task(name="grading.grade_exam_session")
def grade_exam_session(session_id: str) -> None:
    chain(
        finalize_media.si(session_id),
        transcribe_full_session.si(session_id),
        chord(
            group(
                compute_fluency_metrics.si(session_id),
                compute_lexical_metrics.si(session_id),
                compute_grammar_metrics.si(session_id),
                compute_pronunciation_scores.si(session_id),
            ),
            synthesize_band_scores.si(session_id),
        ),
    ).apply_async()
