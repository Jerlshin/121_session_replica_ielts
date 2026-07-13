import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class SessionStatus:
    """Coarse session lifecycle status. Fine-grained exam phase (INTRO,
    PART1_TOPIC_A, ...) is owned by packages/exam-fsm from Phase 3 onward
    and tracked in ExamSession.current_phase."""

    CREATED = "CREATED"
    ACTIVE = "ACTIVE"
    DISCONNECTED = "DISCONNECTED"
    ABORTED = "ABORTED"
    COMPLETED = "COMPLETED"


class ExamSession(Base):
    """Durable session identity (Spec 01 §5.1). The authoritative FSM state
    is folded from exam_session_events; this row is the identity + fast
    pointers (current phase snapshot, resume token, Gemini resumption
    handle), not the source of truth for state."""

    __tablename__ = "exam_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidates.id"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=SessionStatus.CREATED
    )
    current_phase: Mapped[str | None] = mapped_column(String(40), nullable=True)
    resume_token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    gemini_resumption_handle: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
