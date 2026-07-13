import uuid

from sqlalchemy import Boolean, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class TopicSet(Base):
    """One rotating Part 1 topic set for a single slot (Spec 02 §4). Slots
    A/B/C are assigned per session by `exam_content.assign_topic_sets`,
    avoiding repeats via `candidates.previous_topic_sets`."""

    __tablename__ = "topic_sets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    slot: Mapped[str] = mapped_column(String(1), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    questions: Mapped[list] = mapped_column(JSONB, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
