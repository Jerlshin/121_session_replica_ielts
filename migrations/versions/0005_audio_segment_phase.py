"""audio_segments.exam_phase — the raw ExamPhase a turn was spoken in,
captured server-side at the moment the turn's flush is registered (before
any FSM advance that turn might itself trigger — see
app/services/exam_orchestrator.py::on_turn_flush_started). Phase 6's
feature-extraction tasks (Spec 03 §4) need reliable per-phase (Part 1/2/3)
bucketing of transcript words; correlating audio_segments.created_at
against exam_session_events timestamps is racy for exactly the last turn
of every phase (the FSM advances synchronously, before the background
media flush's S3 round-trip even starts), so this is captured directly
instead of reconstructed after the fact.

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-13

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("audio_segments", sa.Column("exam_phase", sa.String(40), nullable=True))


def downgrade() -> None:
    op.drop_column("audio_segments", "exam_phase")
