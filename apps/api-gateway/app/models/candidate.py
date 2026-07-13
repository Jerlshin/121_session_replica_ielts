import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Candidate(Base):
    """Identity record. ID-verification is stored as a result/hash only —
    raw ID images are never retained beyond the policy window (Spec 01 §6)."""

    __tablename__ = "candidates"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    id_verification_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Topic-set ids previously assigned to this candidate (Spec 02 §4) — read
    # and appended to by `exam_content.assign_topic_sets` so a retake never
    # repeats a Part 1 topic set it doesn't have to.
    previous_topic_sets: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
