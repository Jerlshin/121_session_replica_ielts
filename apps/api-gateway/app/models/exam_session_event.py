import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ExamSessionEvent(Base):
    """Append-only event log — the source of truth every FSM snapshot is
    folded from (Spec 01 §5.2). Never updated or deleted; current state is
    always `fold(events)`, which is what makes resume-after-failover a
    query rather than a special case."""

    __tablename__ = "exam_session_events"
    __table_args__ = (UniqueConstraint("session_id", "seq", name="uq_session_seq"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("exam_sessions.id"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
