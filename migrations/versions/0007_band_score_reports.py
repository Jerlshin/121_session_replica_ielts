"""band_score_reports — Phase 7 final synthesized scores, justifications,
evidence references, and human-review flags (Spec 01 §6, Spec 03 §5.6).
One row per session (upserted, same idempotency contract as grading_jobs /
feature_vectors). Stores the full audit trail — the complete JudgeInput,
both raw JudgeOutput passes, and the reconciliation decision — so any
score, flagged or not, can be reviewed against the exact evidence the
model saw (Spec 03 §5.6's "defensible" requirement).

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-13

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "band_score_reports",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("exam_sessions.id"),
            nullable=False,
        ),
        sa.Column("overall_band", sa.Float, nullable=False),
        sa.Column("criterion_scores", postgresql.JSONB, nullable=False),
        sa.Column("judge_input", postgresql.JSONB, nullable=False),
        sa.Column("judge_pass_1", postgresql.JSONB, nullable=False),
        sa.Column("judge_pass_2", postgresql.JSONB, nullable=False),
        sa.Column("reconciliation", postgresql.JSONB, nullable=False),
        sa.Column(
            "flag_for_human_review", sa.Boolean, nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("session_id", name="uq_band_score_report_session"),
    )
    op.create_index("ix_band_score_reports_session_id", "band_score_reports", ["session_id"])


def downgrade() -> None:
    op.drop_table("band_score_reports")
