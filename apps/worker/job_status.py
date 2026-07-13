"""Shared grading_jobs upsert helpers (Spec 01 §6, Spec 03 §2.4) — every
pipeline task's status/result bookkeeping goes through here so the
upsert-not-append idempotency contract is enforced in exactly one place,
not reimplemented per task.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from db import session_scope
from models import GradingJob, GradingJobStatus


def mark_running(session_id: uuid.UUID, task_name: str) -> None:
    with session_scope() as db:
        stmt = insert(GradingJob).values(
            session_id=session_id,
            task_name=task_name,
            status=GradingJobStatus.RUNNING,
            started_at=datetime.now(timezone.utc),
            attempt=1,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["session_id", "task_name"],
            set_={
                "status": GradingJobStatus.RUNNING,
                "started_at": datetime.now(timezone.utc),
                # Increments off the *existing* row's attempt, not the
                # inserted value — this is what makes attempt count
                # accurately across retries and manual re-runs alike.
                "attempt": GradingJob.attempt + 1,
                "error": None,
            },
        )
        db.execute(stmt)


def mark_succeeded(session_id: uuid.UUID, task_name: str, result: dict) -> None:
    with session_scope() as db:
        stmt = insert(GradingJob).values(
            session_id=session_id,
            task_name=task_name,
            status=GradingJobStatus.SUCCEEDED,
            result=result,
            error=None,
            finished_at=datetime.now(timezone.utc),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["session_id", "task_name"],
            set_={
                "status": GradingJobStatus.SUCCEEDED,
                "result": result,
                "error": None,
                "finished_at": datetime.now(timezone.utc),
            },
        )
        db.execute(stmt)


def mark_failed(session_id: uuid.UUID, task_name: str, error: str) -> None:
    with session_scope() as db:
        stmt = insert(GradingJob).values(
            session_id=session_id,
            task_name=task_name,
            status=GradingJobStatus.FAILED,
            error=error,
            finished_at=datetime.now(timezone.utc),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["session_id", "task_name"],
            # Deliberately does not touch `result` — a failed re-run must
            # not clobber a still-valid result from an earlier successful
            # attempt (e.g. finalize_media's canonical_audio_key, which
            # transcribe_full_session may still need to read).
            set_={
                "status": GradingJobStatus.FAILED,
                "error": error,
                "finished_at": datetime.now(timezone.utc),
            },
        )
        db.execute(stmt)


def load_result(session_id: uuid.UUID, task_name: str) -> dict | None:
    with session_scope() as db:
        job = db.scalar(
            select(GradingJob).where(
                GradingJob.session_id == session_id, GradingJob.task_name == task_name
            )
        )
        return job.result if job else None
