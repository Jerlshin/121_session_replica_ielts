import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class GradingJobStatus:
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class GradingJob(Base):
    """Idempotency/status row per (session_id, task_name) — Spec 01 §6,
    Spec 03 §2.4. Every pipeline task upserts its own row here rather than
    appending, which is what makes a targeted solo re-run of just the
    failed stage possible instead of re-running the whole chain. Read-only
    from the gateway side (the internal debug endpoint); only
    `apps/worker`'s tasks write to it."""

    __tablename__ = "grading_jobs"
    __table_args__ = (
        UniqueConstraint("session_id", "task_name", name="uq_grading_job_session_task"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("exam_sessions.id"), nullable=False, index=True
    )
    task_name: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=GradingJobStatus.QUEUED)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
