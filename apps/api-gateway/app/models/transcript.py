import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Transcript(Base):
    """Word-level backline transcript (Spec 01 §4.3/§6) — the sole source
    of truth for grading, never the live Gemini caption lane. `speaker` is
    always "candidate" in v1: Gemini's replies are relayed for playback but
    never persisted server-side, so the canonical audio this is transcribed
    from is candidate-only. `source` is "deepgram" | "whisperx" | "fixture"
    (Spec 03 §3) — read-only from the gateway side; only `apps/worker`'s
    `transcribe_full_session` task writes to it."""

    __tablename__ = "transcripts"
    __table_args__ = (
        UniqueConstraint("session_id", "turn_id", "seq", name="uq_transcript_word_key"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("exam_sessions.id"), nullable=False, index=True
    )
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
