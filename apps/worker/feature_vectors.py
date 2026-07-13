"""Shared feature_vectors upsert helper (Spec 01 §6, Spec 03 §4) — mirrors
job_status.py's pattern exactly: one row per (session_id, criterion,
phase), upserted not appended, so a targeted re-run of a single criterion
never leaves stale duplicate rows behind.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy.dialects.postgresql import insert

from db import session_scope
from models import FeatureVector

CRITERIA = ("fluency", "lexical", "grammar", "pronunciation")
PHASES = ("part1", "part2", "part3", "session")


def write_feature_vector(
    session_id: uuid.UUID, criterion: str, phase: str, features: dict
) -> None:
    if criterion not in CRITERIA:
        raise ValueError(f"unknown criterion {criterion!r}, expected one of {CRITERIA}")
    if phase not in PHASES:
        raise ValueError(f"unknown phase {phase!r}, expected one of {PHASES}")

    with session_scope() as db:
        stmt = insert(FeatureVector).values(
            session_id=session_id, criterion=criterion, phase=phase, features=features
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["session_id", "criterion", "phase"],
            set_={"features": features, "updated_at": datetime.now(timezone.utc)},
        )
        db.execute(stmt)
