import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class VideoSegmentStatus:
    PENDING = "PENDING"
    UPLOADED = "UPLOADED"


class VideoSegment(Base):
    """Proctoring video pointer — never joined to scoring tables (Spec 01
    §3.1, §6). Written via presigned URL directly browser -> object
    storage; this row only tracks the pointer and upload status, the API
    pods never see the video bytes (CLAUDE.md rule 3)."""

    __tablename__ = "video_segments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("exam_sessions.id"), nullable=False, index=True
    )
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=VideoSegmentStatus.PENDING
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
