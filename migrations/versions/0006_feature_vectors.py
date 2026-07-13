"""feature_vectors — Phase 6 computed FC/LR/GRA/P feature JSON per phase +
session aggregate, with provenance tags (Spec 01 §6, Spec 03 §4). One row
per (session_id, criterion, phase), upserted not appended — same
idempotency contract as grading_jobs (Spec 03 §2.4).

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-13

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "feature_vectors",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("exam_sessions.id"),
            nullable=False,
        ),
        sa.Column("criterion", sa.String(20), nullable=False),
        sa.Column("phase", sa.String(20), nullable=False),
        sa.Column("features", postgresql.JSONB, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("session_id", "criterion", "phase", name="uq_feature_vector_key"),
    )
    op.create_index("ix_feature_vectors_session_id", "feature_vectors", ["session_id"])


def downgrade() -> None:
    op.drop_table("feature_vectors")
