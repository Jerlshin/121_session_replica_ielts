"""audio_segments, video_segments — the media spine's storage pointers

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-13

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "audio_segments",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("exam_sessions.id"),
            nullable=False,
        ),
        sa.Column("turn_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("storage_key", sa.String(512), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("byte_size", sa.BigInteger, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("session_id", "turn_id", "seq", name="uq_audio_segment_key"),
    )
    op.create_index("ix_audio_segments_session_id", "audio_segments", ["session_id"])

    op.create_table(
        "video_segments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("exam_sessions.id"),
            nullable=False,
        ),
        sa.Column("storage_key", sa.String(512), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_video_segments_session_id", "video_segments", ["session_id"])


def downgrade() -> None:
    op.drop_table("video_segments")
    op.drop_table("audio_segments")
