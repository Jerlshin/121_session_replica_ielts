"""Shared band_score_reports upsert helper (Spec 01 §6, Spec 03 §5.6) —
mirrors job_status.py/feature_vectors.py's pattern: one row per
session_id, upserted not appended, so a re-run of synthesize_band_scores
replaces rather than duplicates the report. Persists the complete audit
trail — the JudgeInput the model saw, both raw JudgeOutput passes, and the
reconciliation decision — so every score, flagged or not, stays
defensible (Spec 03 §5.6).
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy.dialects.postgresql import insert

from db import session_scope
from models import BandScoreReport
from providers.scoring_llm import JudgeInput, JudgeOutput


def write_band_score_report(
    session_id: uuid.UUID,
    judge_input: JudgeInput,
    judge_pass_1: JudgeOutput,
    judge_pass_2: JudgeOutput,
    reconciliation: dict,
) -> None:
    values = {
        "overall_band": reconciliation["overall_band"],
        "criterion_scores": reconciliation["final_scores"],
        "judge_input": judge_input.model_dump(mode="json"),
        "judge_pass_1": judge_pass_1.model_dump(mode="json"),
        "judge_pass_2": judge_pass_2.model_dump(mode="json"),
        "reconciliation": reconciliation,
        "flag_for_human_review": reconciliation["flag_for_human_review"],
    }
    with session_scope() as db:
        stmt = insert(BandScoreReport).values(session_id=session_id, **values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["session_id"],
            set_={**values, "updated_at": datetime.now(timezone.utc)},
        )
        db.execute(stmt)
