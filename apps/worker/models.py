"""Sync-mapped mirrors of the subset of the shared Postgres schema this
worker actually touches (migrations 0002, 0004, 0007). Deliberately never
imports/queries VideoSegment: video is proctoring evidence only and has no
route into the grading pipeline (Spec 01 §3.1, CLAUDE.md rule 3).
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from db import Base

# session_id columns below are deliberately plain UUID, not
# ForeignKey("exam_sessions.id") — an ORM-level FK to a table outside this
# Base's metadata breaks SQLAlchemy's flush-order resolution. The real FK
# constraint already exists at the DB level via the centralized Alembic
# migration; this is just about what the ORM needs to know for its own
# sake. ExamSession/Candidate below are themselves mapped (Phase 7 needs
# candidate identity for the judge input, Spec 03 §5.3) but only the
# handful of columns this app actually reads — not a full mirror of the
# gateway's async models.


class ExamSession(Base):
    __tablename__ = "exam_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    candidate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)


class Candidate(Base):
    __tablename__ = "candidates"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)


class AudioSegment(Base):
    __tablename__ = "audio_segments"
    __table_args__ = (
        UniqueConstraint("session_id", "turn_id", "seq", name="uq_audio_segment_key"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    turn_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Raw ExamPhase this turn was spoken in (Phase 6, Spec 03 §4's
    # per-phase bucketing) — see api-gateway's exam_orchestrator.py::
    # on_turn_flush_started for why this is captured directly rather than
    # reconstructed from event timestamps.
    exam_phase: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class GradingJobStatus:
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class GradingJob(Base):
    __tablename__ = "grading_jobs"
    __table_args__ = (
        UniqueConstraint("session_id", "task_name", name="uq_grading_job_session_task"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
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


class Transcript(Base):
    __tablename__ = "transcripts"
    __table_args__ = (
        UniqueConstraint("session_id", "turn_id", "seq", name="uq_transcript_word_key"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    turn_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    word: Mapped[str] = mapped_column(Text, nullable=False)
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    speaker: Mapped[str] = mapped_column(String(20), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class FeatureVector(Base):
    """Computed FC/LR/GRA/P feature JSON per phase + session aggregate,
    with provenance tags (Spec 01 §6, Spec 03 §4) — one row per
    (session_id, criterion, phase), upserted not appended, same
    idempotency contract as GradingJob."""

    __tablename__ = "feature_vectors"
    __table_args__ = (
        UniqueConstraint("session_id", "criterion", "phase", name="uq_feature_vector_key"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    criterion: Mapped[str] = mapped_column(String(20), nullable=False)
    phase: Mapped[str] = mapped_column(String(20), nullable=False)
    features: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class BandScoreReport(Base):
    """Final synthesized scores, justifications, evidence references, and
    human-review flags (Spec 01 §6, Spec 03 §5.6) — one row per session,
    upserted not appended. Stores the complete audit trail (JudgeInput +
    both raw JudgeOutput passes + the reconciliation decision) so any
    score, flagged or not, can be reviewed against the exact evidence the
    model saw."""

    __tablename__ = "band_score_reports"
    __table_args__ = (UniqueConstraint("session_id", name="uq_band_score_report_session"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    overall_band: Mapped[float] = mapped_column(Float, nullable=False)
    criterion_scores: Mapped[list] = mapped_column(JSONB, nullable=False)
    judge_input: Mapped[dict] = mapped_column(JSONB, nullable=False)
    judge_pass_1: Mapped[dict] = mapped_column(JSONB, nullable=False)
    judge_pass_2: Mapped[dict] = mapped_column(JSONB, nullable=False)
    reconciliation: Mapped[dict] = mapped_column(JSONB, nullable=False)
    flag_for_human_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
