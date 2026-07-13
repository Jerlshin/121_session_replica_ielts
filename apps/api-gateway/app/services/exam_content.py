"""Content selection for Part 1/Part 2/Part 3 (Spec 02 §3.1, §4). This is
I/O (DB reads/writes) and therefore deliberately kept out of
`packages/exam-fsm` — the pure package only knows about phases and events,
never about which cue card or topic set was chosen.
"""
import random

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Candidate, CueCard, TopicSet

SLOTS = ("A", "B", "C")


async def select_cue_card(db: AsyncSession) -> CueCard:
    """Picks one active cue card at random. One per session, bound once on
    entering PART2_CUECARD_PRESENT (Spec 02 §1's persisted-artifact column)."""
    cards = (await db.scalars(select(CueCard).where(CueCard.active.is_(True)))).all()
    if not cards:
        raise RuntimeError("no active cue_cards available — content bank is empty")
    return random.choice(cards)


async def assign_topic_sets(db: AsyncSession, candidate: Candidate) -> dict[str, TopicSet]:
    """Assigns one topic set per Part 1 slot (A/B/C), avoiding any id
    already in `candidate.previous_topic_sets` where an unused option
    exists (Spec 02 §4: "avoiding item repetition across a candidate's
    retakes"). Appends the newly-chosen ids to that history."""
    previously_used = set(candidate.previous_topic_sets or [])
    assigned: dict[str, TopicSet] = {}

    for slot in SLOTS:
        options = (
            await db.scalars(
                select(TopicSet).where(TopicSet.slot == slot, TopicSet.active.is_(True))
            )
        ).all()
        if not options:
            raise RuntimeError(f"no active topic_sets available for slot {slot!r}")

        unused = [t for t in options if str(t.id) not in previously_used]
        # If every option for this slot has already been used, fall back to
        # the full set rather than failing the exam — repetition is a soft
        # constraint, not a hard one.
        chosen = random.choice(unused or options)
        assigned[slot] = chosen

    candidate.previous_topic_sets = [
        *candidate.previous_topic_sets,
        *[str(t.id) for t in assigned.values()],
    ]
    return assigned


def topic_set_ids_payload(assigned: dict[str, TopicSet]) -> dict[str, str]:
    return {slot: str(topic_set.id) for slot, topic_set in assigned.items()}
