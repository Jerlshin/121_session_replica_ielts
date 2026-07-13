"""initial schema: candidates, exam_sessions, exam_session_events

Revision ID: 0001
Revises:
Create Date: 2026-07-13

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "candidates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.Column("id_verification_hash", sa.String(128), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("email", name="uq_candidates_email"),
    )
    op.create_index("ix_candidates_email", "candidates", ["email"])

    op.create_table(
        "exam_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "candidate_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("candidates.id"),
            nullable=False,
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default="CREATED"),
        sa.Column("current_phase", sa.String(40), nullable=True),
        sa.Column("resume_token", sa.String(64), nullable=False),
        sa.Column("gemini_resumption_handle", sa.String(512), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("resume_token", name="uq_exam_sessions_resume_token"),
    )
    op.create_index("ix_exam_sessions_candidate_id", "exam_sessions", ["candidate_id"])

    op.create_table(
        "exam_session_events",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("exam_sessions.id"),
            nullable=False,
        ),
        sa.Column("seq", sa.BigInteger, nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("session_id", "seq", name="uq_session_seq"),
    )
    op.create_index("ix_exam_session_events_session_id", "exam_session_events", ["session_id"])


def downgrade() -> None:
    op.drop_table("exam_session_events")
    op.drop_table("exam_sessions")
    op.drop_table("candidates")
