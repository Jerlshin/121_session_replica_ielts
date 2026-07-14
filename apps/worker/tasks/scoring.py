"""synthesize_band_scores (Spec 03 §2.2, §5): the LLM Rubric Judge stage.
Assembles Phase 6's deterministic feature vectors plus the candidate's
per-phase transcript into a JudgeInput (Evidence Before Judgment, CLAUDE.md
rule 6 — the judge never receives a bare transcript), runs two independent
ScoringLLM passes, reconciles them (self_consistency_reconciliation, Spec
03 §5.6), and persists the full audit trail to band_score_reports.

Reads feature_vectors/transcripts/exam_sessions directly from Postgres
rather than a chain-passed argument, same rationale as
transcribe_full_session (see pipelines/grading_pipeline.py's docstring) —
a standalone re-run (`synthesize_band_scores.delay(session_id)`) works
even if none of the Phase 6 tasks were just re-run in the same chain, and
naturally tolerates whichever of those tasks did or didn't succeed by
treating their output as simply absent (`feature_status: "missing"`)
rather than failing to build a JudgeInput at all.
"""
import logging
import uuid

from sqlalchemy import select

from band_score_reports import write_band_score_report
from celery_app import app
from config import settings
from db import session_scope
from job_status import mark_failed, mark_running, mark_succeeded
from models import Candidate, ExamSession, FeatureVector
from nlp_common import PHASE_BUCKET_ORDER, load_words_by_phase
from providers.scoring_llm import JudgeInput, OpenAIScoringLLM, PhaseEvidence, ScoringLLM
from reconciliation import self_consistency_reconciliation
from rubric_assets import load_rubric_reference

logger = logging.getLogger("worker.tasks.scoring")

TASK_NAME = "synthesize_band_scores"

# feature_vectors.CRITERIA (short) -> CriterionScore.criterion (Spec 03 §5.4)
_CRITERION_TO_JUDGE_NAME = {
    "fluency": "fluency_coherence",
    "lexical": "lexical_resource",
    "grammar": "grammatical_range_accuracy",
    "pronunciation": "pronunciation",
}


def _load_feature_vectors(session_id: uuid.UUID) -> dict[tuple[str, str], dict]:
    with session_scope() as db:
        rows = db.scalars(select(FeatureVector).where(FeatureVector.session_id == session_id)).all()
        return {(row.phase, row.criterion): row.features for row in rows}


def _load_candidate_display_name(session_id: uuid.UUID) -> str:
    with session_scope() as db:
        exam_session = db.get(ExamSession, session_id)
        if exam_session is None:
            raise RuntimeError(f"no exam_sessions row for session={session_id}")
        candidate = db.get(Candidate, exam_session.candidate_id)
        if candidate is None:
            raise RuntimeError(f"no candidates row for candidate={exam_session.candidate_id}")
        return candidate.full_name


def _build_judge_input(session_id: uuid.UUID) -> JudgeInput:
    feature_vectors = _load_feature_vectors(session_id)
    words_by_phase = load_words_by_phase(session_id)

    phases = []
    for phase in PHASE_BUCKET_ORDER:
        words = words_by_phase.get(phase)
        if not words:
            continue
        phases.append(
            PhaseEvidence(
                phase=phase,
                transcript_text=" ".join(w.word for w in words),
                fluency_features=feature_vectors.get((phase, "fluency"), {}),
                lexical_features=feature_vectors.get((phase, "lexical"), {}),
                grammar_features=feature_vectors.get((phase, "grammar"), {}),
                pronunciation_features=feature_vectors.get((phase, "pronunciation"), {}),
            )
        )

    session_aggregate = {
        judge_name: feature_vectors.get(("session", short_name), {})
        for short_name, judge_name in _CRITERION_TO_JUDGE_NAME.items()
    }
    feature_status = {
        judge_name: ("ok" if ("session", short_name) in feature_vectors else "missing")
        for short_name, judge_name in _CRITERION_TO_JUDGE_NAME.items()
    }

    return JudgeInput(
        session_id=session_id,
        candidate_display_name=_load_candidate_display_name(session_id),
        phases=phases,
        session_aggregate=session_aggregate,
        rubric_reference=load_rubric_reference(settings.rubric_assets_dir),
        feature_status=feature_status,
    )


@app.task(name="tasks.scoring.synthesize_band_scores", bind=True, max_retries=3, time_limit=600)
def synthesize_band_scores(self, session_id: str, *, scoring_llm: ScoringLLM | None = None) -> dict:
    session_uuid = uuid.UUID(session_id)
    mark_running(session_uuid, TASK_NAME)

    try:
        judge_input = _build_judge_input(session_uuid)
        llm = scoring_llm or OpenAIScoringLLM()

        # Two independent passes (Spec 03 §5.6) — see OpenAIScoringLLM's
        # docstring for why these aren't literally low-temperature calls.
        pass_1 = llm.score(judge_input)
        pass_2 = llm.score(judge_input)

        reconciliation = self_consistency_reconciliation(
            pass_1, pass_2, threshold=settings.self_consistency_band_disagreement_threshold
        )
        write_band_score_report(session_uuid, judge_input, pass_1, pass_2, reconciliation)

        result = {
            "overall_band": reconciliation["overall_band"],
            "flag_for_human_review": reconciliation["flag_for_human_review"],
        }
        mark_succeeded(session_uuid, TASK_NAME, result)
        return result

    except Exception as exc:
        logger.exception("synthesize_band_scores failed session=%s", session_id)
        mark_failed(session_uuid, TASK_NAME, str(exc))
        raise self.retry(exc=exc) from exc
