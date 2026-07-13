import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class AudioSegment(Base):
    """Per-turn raw audio pointer into object storage (Spec 01 §6/§7).

    Idempotency key is (session_id, turn_id, seq) — a retried flush for a
    turn that already landed is a no-op, not a duplicate (Spec 01 §5.5)."""

    __tablename__ = "audio_segments"
    __table_args__ = (
        UniqueConstraint("session_id", "turn_id", "seq", name="uq_audio_segment_key"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("exam_sessions.id"), nullable=False, index=True
    )
    turn_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)  # sha256 hex
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # The raw ExamPhase this turn was spoken in, captured server-side at
    # flush-registration time (Phase 6, Spec 03 §4's per-phase bucketing) —
    # see exam_orchestrator.py::on_turn_flush_started for why this is
    # captured directly rather than reconstructed from event timestamps.
    exam_phase: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
