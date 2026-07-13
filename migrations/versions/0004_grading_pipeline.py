"""grading_jobs, transcripts — Phase 5 backline transcription pipeline
(Spec 03 §2.4, §3; Spec 01 §6). grading_jobs is the idempotency/status
table every pipeline task upserts into (keyed by session_id+task_name,
never appended) so a failed stage supports a targeted solo re-run instead
of re-running the whole chain. transcripts is the word-level canonical
transcript — the sole source of truth for grading (Spec 01 §4.3).

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-13

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "grading_jobs",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("exam_sessions.id"),
            nullable=False,
        ),
        sa.Column("task_name", sa.String(100), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="QUEUED"),
        sa.Column("attempt", sa.Integer, nullable=False, server_default="1"),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("result", postgresql.JSONB, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("session_id", "task_name", name="uq_grading_job_session_task"),
    )
    op.create_index("ix_grading_jobs_session_id", "grading_jobs", ["session_id"])

    op.create_table(
        "transcripts",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("exam_sessions.id"),
            nullable=False,
        ),
        sa.Column("turn_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("word", sa.Text, nullable=False),
        sa.Column("start_ms", sa.Integer, nullable=False),
        sa.Column("end_ms", sa.Integer, nullable=False),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("speaker", sa.String(20), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("session_id", "turn_id", "seq", name="uq_transcript_word_key"),
    )
    op.create_index("ix_transcripts_session_id", "transcripts", ["session_id"])


def downgrade() -> None:
    op.drop_table("transcripts")
    op.drop_table("grading_jobs")
