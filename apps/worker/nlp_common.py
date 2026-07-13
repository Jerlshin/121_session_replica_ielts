"""Shared plumbing for Phase 6's feature-extraction tasks (Spec 03 §4):
loading transcript words bucketed by exam phase, a cached spaCy pipeline,
utterance segmentation for unpunctuated spoken transcripts, syllable
counting, and word-frequency lookups. Nothing here is a Celery task —
these are pure/IO helpers the four `compute_*` tasks all depend on.
"""
import re
import uuid
from dataclasses import dataclass
from functools import lru_cache

import pronouncing
import spacy
from sqlalchemy import select
from wordfreq import zipf_frequency

from db import session_scope
from models import AudioSegment, Transcript

# Spec 02 §1's phase table, bucketed into the three IELTS parts Spec 03 §4
# actually scores ("per phase (Part 1/2/3) and as a session-level
# aggregate"). Round-off questions are structurally still the Part 2 block
# in Spec 02's phase table; other phases (INIT_DEVICE_CHECK,
# ID_VERIFICATION, INTRO, PART2_CUECARD_PRESENT, PART2_PREP, CLOSE,
# FINALIZING, COMPLETE) have no scored candidate turns.
_PHASE_BUCKETS = {
    "PART1_TOPIC_A": "part1",
    "PART1_TOPIC_B": "part1",
    "PART1_TOPIC_C": "part1",
    "PART2_LONG_TURN": "part2",
    "PART2_ROUNDOFF": "part2",
    "PART3_DISCUSSION": "part3",
}

PHASE_BUCKET_ORDER = ("part1", "part2", "part3")


@dataclass(frozen=True)
class TranscriptWord:
    turn_id: uuid.UUID
    seq: int
    word: str
    start_ms: int
    end_ms: int
    confidence: float
    source: str


def load_words_by_phase(session_id: uuid.UUID) -> dict[str, list[TranscriptWord]]:
    """Returns e.g. {"part1": [...], "part2": [...], "part3": [...]} — only
    buckets that actually have words are present. Each list is ordered by
    `start_ms` in the canonical stitched audio's timeline (Spec 01 §4.3)."""
    with session_scope() as db:
        turn_phase = dict(
            db.execute(
                select(AudioSegment.turn_id, AudioSegment.exam_phase).where(
                    AudioSegment.session_id == session_id
                )
            ).all()
        )
        rows = db.scalars(
            select(Transcript)
            .where(Transcript.session_id == session_id)
            .order_by(Transcript.start_ms)
        ).all()
        words = [
            TranscriptWord(
                turn_id=row.turn_id,
                seq=row.seq,
                word=row.word,
                start_ms=row.start_ms,
                end_ms=row.end_ms,
                confidence=row.confidence,
                source=row.source,
            )
            for row in rows
        ]

    buckets: dict[str, list[TranscriptWord]] = {}
    for w in words:
        bucket = _PHASE_BUCKETS.get(turn_phase.get(w.turn_id))
        if bucket is None:
            continue
        buckets.setdefault(bucket, []).append(w)
    return buckets


@lru_cache(maxsize=1)
def spacy_nlp():
    return spacy.load("en_core_web_sm")


def parse_words(word_strings: list[str]) -> spacy.tokens.Doc:
    """Parses a *pre-tokenized* list of words through spaCy's tagger and
    parser, guaranteeing `doc[i]` corresponds exactly to `word_strings[i]`
    — critical for mapping parse structure back onto transcript
    timestamps (fluency's pause-placement, grammar's T-unit segmentation).
    A naive `nlp(" ".join(word_strings))` call can't guarantee this once
    spaCy's own tokenizer might split or merge tokens differently
    (contractions, punctuation-free ASR output, etc.)."""
    nlp = spacy_nlp()
    doc = spacy.tokens.Doc(nlp.vocab, words=word_strings)
    for _, component in nlp.pipeline:
        doc = component(doc)
    return doc


def segment_into_utterances(
    words: list[TranscriptWord], *, pause_threshold_s: float = 0.6
) -> list[list[TranscriptWord]]:
    """Splits a phase's word list into utterance-like chunks, both at a
    turn-id change (canonical.flac concatenates turns back-to-back with no
    gap at all in the stitched timeline — Spec 01 §4.3 — so a turn
    boundary carries no pause signal of its own but is always a genuine
    utterance break) and at a long within-turn pause. Spoken, largely
    unpunctuated ASR output needs *some* segmentation signal before being
    handed to spaCy's parser, or a whole 2-minute turn parses as a single
    "sentence" with one ROOT — undercounting T-units/clauses (Spec 03
    §4.3) and defeating clause-boundary pause classification (§4.1).
    """
    if not words:
        return []
    chunks: list[list[TranscriptWord]] = [[words[0]]]
    for prev, curr in zip(words, words[1:]):
        gap_s = (curr.start_ms - prev.end_ms) / 1000
        new_turn = curr.turn_id != prev.turn_id
        if new_turn or gap_s >= pause_threshold_s:
            chunks.append([])
        chunks[-1].append(curr)
    return chunks


_NON_ALPHA = re.compile(r"[^a-zA-Z']")
_VOWEL_GROUPS = re.compile(r"[aeiouy]+", re.IGNORECASE)


def _clean_word(word: str) -> str:
    return _NON_ALPHA.sub("", word)


def syllable_count(word: str) -> int:
    cleaned = _clean_word(word)
    if not cleaned:
        return 0
    phones = pronouncing.phones_for_word(cleaned.lower())
    if phones:
        return pronouncing.syllable_count(phones[0])
    # Heuristic fallback for OOV tokens (unusual proper nouns, filled
    # pauses, ASR artifacts not in the CMU dict): vowel-group count,
    # floored at 1 for any non-empty alphabetic token.
    return max(1, len(_VOWEL_GROUPS.findall(cleaned)))


def zipf(word: str) -> float:
    cleaned = _clean_word(word).lower()
    if not cleaned:
        return 0.0
    return zipf_frequency(cleaned, "en")
