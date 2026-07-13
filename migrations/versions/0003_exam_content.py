"""topic_sets, cue_cards — Part 1/Part 2/Part 3 content banks (Spec 02 §3.1,
§4), plus the FSM columns that bind selected content to a session and track
a candidate's topic-set history to avoid item repetition across retakes.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-13

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Fixed seed ids so `alembic upgrade head` is reproducible across a fresh
# database rather than randomly generated at migration-run time (Spec 04 §3
# anti-regression spirit: content, like schema, should be a reviewable diff).
_TOPIC_A1 = "47003ca5-451c-4972-8d88-61b0a4176fc8"
_TOPIC_A2 = "0892c3d4-8f9c-4bf6-8d98-e28c15b4bbcf"
_TOPIC_B1 = "adf2012b-3fd4-45a5-bd00-fceee0b76cbf"
_TOPIC_B2 = "e40973b8-f6a0-4b95-b7ca-4ea10047be25"
_TOPIC_C1 = "15dc72aa-9e27-4188-aac0-75316512b515"
_TOPIC_C2 = "480ec61d-1f81-4381-ba30-03e7a974a2a8"
_CUE_1 = "cd40152c-0244-46e5-b030-7b6f9594d2cd"
_CUE_2 = "47ba3bae-b035-4d82-92f8-57ae0ffd5462"


def upgrade() -> None:
    topic_sets = op.create_table(
        "topic_sets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("slot", sa.String(1), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("questions", postgresql.JSONB, nullable=False),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.CheckConstraint("slot IN ('A', 'B', 'C')", name="ck_topic_sets_slot"),
    )
    op.create_index("ix_topic_sets_slot", "topic_sets", ["slot"])

    cue_cards = op.create_table(
        "cue_cards",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("topic", sa.Text, nullable=False),
        sa.Column("bullets", postgresql.JSONB, nullable=False),
        sa.Column("linked_part3_themes", postgresql.JSONB, nullable=False),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
    )

    op.add_column(
        "candidates",
        sa.Column(
            "previous_topic_sets", postgresql.JSONB, nullable=False, server_default="[]"
        ),
    )
    op.add_column(
        "exam_sessions",
        sa.Column(
            "cue_card_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cue_cards.id"),
            nullable=True,
        ),
    )
    op.add_column(
        "exam_sessions",
        sa.Column("topic_set_ids", postgresql.JSONB, nullable=True),
    )

    op.bulk_insert(
        topic_sets,
        [
            {
                "id": _TOPIC_A1,
                "slot": "A",
                "title": "Hometown",
                "questions": [
                    "Where is your hometown?",
                    "What do you like most about your hometown?",
                    "Has your hometown changed much since you were a child?",
                    "Would you like to live somewhere else in the future?",
                ],
            },
            {
                "id": _TOPIC_A2,
                "slot": "A",
                "title": "Daily Routine",
                "questions": [
                    "Can you describe your typical daily routine?",
                    "What part of your day do you enjoy the most?",
                    "Has your daily routine changed recently?",
                    "Do you prefer a fixed routine or a flexible one?",
                ],
            },
            {
                "id": _TOPIC_B1,
                "slot": "B",
                "title": "Free Time",
                "questions": [
                    "What do you like to do in your free time?",
                    "Do you prefer spending free time alone or with others?",
                    "How has the way you spend free time changed over the years?",
                    "Is free time important to you? Why?",
                ],
            },
            {
                "id": _TOPIC_B2,
                "slot": "B",
                "title": "Food",
                "questions": [
                    "What kind of food do you enjoy eating?",
                    "Do you prefer eating at home or at restaurants?",
                    "Have your food preferences changed since you were younger?",
                    "Is cooking something you enjoy doing?",
                ],
            },
            {
                "id": _TOPIC_C1,
                "slot": "C",
                "title": "Technology",
                "questions": [
                    "How often do you use a smartphone?",
                    "What apps or devices do you find most useful?",
                    "Has technology changed the way you communicate with friends?",
                    "Do you think you rely on technology too much?",
                ],
            },
            {
                "id": _TOPIC_C2,
                "slot": "C",
                "title": "Weather",
                "questions": [
                    "What's the weather usually like where you live?",
                    "What's your favorite kind of weather?",
                    "Does the weather affect your mood?",
                    "How do people in your country usually deal with bad weather?",
                ],
            },
        ],
    )

    op.bulk_insert(
        cue_cards,
        [
            {
                "id": _CUE_1,
                "topic": "Describe a skill you learned that you found difficult at first.",
                "bullets": [
                    "what the skill was",
                    "why you decided to learn it",
                    "what difficulties you had",
                    "and explain how you felt once you had learned it",
                ],
                "linked_part3_themes": [
                    "Why do some people give up on learning new skills more easily than others?",
                    "Do you think schools should teach practical skills as well as academic subjects?",
                    "How has technology changed the way people learn new skills?",
                ],
            },
            {
                "id": _CUE_2,
                "topic": "Describe a memorable trip you took.",
                "bullets": [
                    "where you went",
                    "who you went with",
                    "what you did there",
                    "and explain why it was memorable",
                ],
                "linked_part3_themes": [
                    "How has tourism changed in your country in recent years?",
                    "What are the benefits and drawbacks of traveling in large groups?",
                    "Do you think travel broadens a person's understanding of the world?",
                ],
            },
        ],
    )


def downgrade() -> None:
    op.drop_column("exam_sessions", "topic_set_ids")
    op.drop_column("exam_sessions", "cue_card_id")
    op.drop_column("candidates", "previous_topic_sets")
    op.drop_table("cue_cards")
    op.drop_table("topic_sets")
