import uuid

from sqlalchemy import Boolean, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class CueCard(Base):
    """A versioned, deterministic Part 2 cue card (Spec 02 §3.1) — never
    spoken freeform by Gemini; pushed to the client as structured JSON and
    simultaneously injected as a phase directive so both see the same
    content. `linked_part3_themes` binds the Part 3 discussion to what the
    candidate just spoke about (Spec 02 §4)."""

    __tablename__ = "cue_cards"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    bullets: Mapped[list] = mapped_column(JSONB, nullable=False)
    linked_part3_themes: Mapped[list] = mapped_column(JSONB, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
