"""compute_fluency_metrics (Spec 03 §2.2, §4.1): Speech Rate, Articulation
Rate, Phonation Time Ratio, Mean Length of Run, silent/filled pause rates,
pause placement, self-repair rate, and discourse marker usage — all rule-
based/deterministic over `transcripts` word timestamps, no external
vendor. Computed per phase (part1/part2/part3) and as a session aggregate
(Spec 03 §4.1's "Output" line), written to `feature_vectors`.
"""
import uuid

from celery_app import app
from feature_vectors import write_feature_vector
from nlp_common import (
    PHASE_BUCKET_ORDER,
    TranscriptWord,
    load_words_by_phase,
    parse_words,
    segment_into_utterances,
    syllable_count,
)
from tasks.nlp.lexicons import (
    DISCOURSE_MARKERS,
    FILLED_PAUSE_BIGRAMS,
    FILLED_PAUSES,
    SELF_REPAIR_BIGRAMS,
    SELF_REPAIR_MARKERS,
)

TASK_NAME = "compute_fluency_metrics"

MICRO_PAUSE_FLOOR_S = 0.3
MACRO_PAUSE_FLOOR_S = 1.0
# Pauses at/above this cross a chunking boundary in segment_into_utterances
# and are therefore classified as clause-boundary by construction — see
# _classify_pause_placement's docstring.
CHUNKING_PAUSE_THRESHOLD_S = 0.6


def _normalize(word: str) -> str:
    return "".join(ch for ch in word.lower() if ch.isalpha())


def _detect_pauses(words: list[TranscriptWord]) -> list[dict]:
    """Within-turn gaps >= 0.3s (Spec 03 §4.1). A turn boundary is never a
    pause — canonical.flac concatenates turns with zero gap in the
    stitched timeline (Spec 01 §4.3), so a "gap" there is an artifact of
    stitching, not a candidate's real silence."""
    pauses = []
    for i, (prev, curr) in enumerate(zip(words, words[1:])):
        if curr.turn_id != prev.turn_id:
            continue
        gap_s = (curr.start_ms - prev.end_ms) / 1000
        if gap_s >= MICRO_PAUSE_FLOOR_S:
            pauses.append({"after_word_index": i, "gap_s": gap_s})
    return pauses


def _classify_pause_placement(words: list[TranscriptWord], pauses: list[dict]) -> dict:
    """Clause-boundary vs. mid-clause (Spec 03 §4.1) — a pause is
    clause-boundary if (a) its gap is long enough to have itself ended an
    utterance chunk (>= CHUNKING_PAUSE_THRESHOLD_S, so it's a boundary by
    construction), or (b) the word immediately before it is the rightmost
    token of its own dependency subtree (nothing more syntactically "owed"
    right after it — a standard proxy for a clause/phrase boundary).
    Everything else is mid-clause."""
    chunks = segment_into_utterances(words, pause_threshold_s=CHUNKING_PAUSE_THRESHOLD_S)
    boundary_after: dict[int, bool] = {}
    offset = 0
    for chunk in chunks:
        doc = parse_words([w.word for w in chunk])
        for local_i, token in enumerate(doc):
            boundary_after[offset + local_i] = token.i == token.right_edge.i
        offset += len(chunk)

    clause_boundary = 0
    mid_clause = 0
    for pause in pauses:
        if pause["gap_s"] >= CHUNKING_PAUSE_THRESHOLD_S or boundary_after.get(
            pause["after_word_index"], False
        ):
            clause_boundary += 1
        else:
            mid_clause += 1
    return {"clause_boundary": clause_boundary, "mid_clause": mid_clause}


def _filled_pause_count(normalized_words: list[str]) -> int:
    count = sum(1 for w in normalized_words if w in FILLED_PAUSES)
    for a, b in zip(normalized_words, normalized_words[1:]):
        if (a, b) in FILLED_PAUSE_BIGRAMS:
            count += 1
    return count


def _self_repair_count(normalized_words: list[str]) -> int:
    count = sum(1 for w in normalized_words if w in SELF_REPAIR_MARKERS)
    for a, b in zip(normalized_words, normalized_words[1:]):
        if (a, b) in SELF_REPAIR_BIGRAMS:
            count += 1
    # Immediate n-gram repetition (candidate repeats the same word back to
    # back while restarting a thought) — a second, independent repair signal.
    for prev, curr in zip(normalized_words, normalized_words[1:]):
        if prev and prev == curr:
            count += 1
    return count


def _discourse_marker_stats(normalized_words: list[str]) -> dict:
    used = [w for w in normalized_words if w in DISCOURSE_MARKERS]
    return {"count": len(used), "distinct_types": len(set(used))}


def _metrics_for_words(words: list[TranscriptWord]) -> dict:
    if not words:
        return _empty_metrics()

    total_words = len(words)
    total_syllables = sum(syllable_count(w.word) for w in words)
    phonation_time_s = sum((w.end_ms - w.start_ms) for w in words) / 1000

    # Total turn duration: span of each turn (first word start -> last
    # word end), summed across turns — the denominator for Speech Rate
    # includes pausing, unlike phonation time.
    turn_spans: dict[uuid.UUID, list[int]] = {}
    for w in words:
        span = turn_spans.setdefault(w.turn_id, [w.start_ms, w.end_ms])
        span[0] = min(span[0], w.start_ms)
        span[1] = max(span[1], w.end_ms)
    total_turn_duration_s = sum((end - start) for start, end in turn_spans.values()) / 1000

    pauses = _detect_pauses(words)
    micro_pauses = [p for p in pauses if p["gap_s"] < MACRO_PAUSE_FLOOR_S]
    macro_pauses = [p for p in pauses if p["gap_s"] >= MACRO_PAUSE_FLOOR_S]
    placement = _classify_pause_placement(words, pauses)

    runs = segment_into_utterances(words, pause_threshold_s=MICRO_PAUSE_FLOOR_S)
    mean_length_of_run = sum(len(r) for r in runs) / len(runs) if runs else 0.0

    normalized = [_normalize(w.word) for w in words]

    return {
        "speech_rate_wpm": round(total_words / total_turn_duration_s * 60, 2)
        if total_turn_duration_s > 0
        else 0.0,
        "articulation_rate_syll_per_s": round(total_syllables / phonation_time_s, 2)
        if phonation_time_s > 0
        else 0.0,
        "phonation_time_ratio": round(phonation_time_s / total_turn_duration_s, 3)
        if total_turn_duration_s > 0
        else 0.0,
        "mean_length_of_run": round(mean_length_of_run, 2),
        "silent_pause_rate_per_100_words": round(len(pauses) / total_words * 100, 2),
        "micro_pause_rate_per_100_words": round(len(micro_pauses) / total_words * 100, 2),
        "macro_pause_rate_per_100_words": round(len(macro_pauses) / total_words * 100, 2),
        "pause_placement": placement,
        "filled_pause_rate_per_100_words": round(
            _filled_pause_count(normalized) / total_words * 100, 2
        ),
        "self_repair_rate_per_100_words": round(
            _self_repair_count(normalized) / total_words * 100, 2
        ),
        "discourse_marker_usage": _discourse_marker_stats(normalized),
        "total_words": total_words,
        "total_turn_duration_s": round(total_turn_duration_s, 2),
    }


def _empty_metrics() -> dict:
    return {
        "speech_rate_wpm": 0.0,
        "articulation_rate_syll_per_s": 0.0,
        "phonation_time_ratio": 0.0,
        "mean_length_of_run": 0.0,
        "silent_pause_rate_per_100_words": 0.0,
        "micro_pause_rate_per_100_words": 0.0,
        "macro_pause_rate_per_100_words": 0.0,
        "pause_placement": {"clause_boundary": 0, "mid_clause": 0},
        "filled_pause_rate_per_100_words": 0.0,
        "self_repair_rate_per_100_words": 0.0,
        "discourse_marker_usage": {"count": 0, "distinct_types": 0},
        "total_words": 0,
        "total_turn_duration_s": 0.0,
    }


@app.task(name="tasks.nlp.compute_fluency_metrics", bind=True, max_retries=3, time_limit=120)
def compute_fluency_metrics(self, session_id: str) -> dict:
    session_uuid = uuid.UUID(session_id)
    try:
        words_by_phase = load_words_by_phase(session_uuid)

        results = {}
        session_words: list[TranscriptWord] = []
        for phase in PHASE_BUCKET_ORDER:
            phase_words = words_by_phase.get(phase, [])
            session_words.extend(phase_words)
            metrics = _metrics_for_words(phase_words)
            metrics["provenance"] = {"source": "rule_based", "word_count": len(phase_words)}
            write_feature_vector(session_uuid, "fluency", phase, metrics)
            results[phase] = metrics

        session_metrics = _metrics_for_words(session_words)
        session_metrics["provenance"] = {
            "source": "rule_based",
            "word_count": len(session_words),
        }
        write_feature_vector(session_uuid, "fluency", "session", session_metrics)
        results["session"] = session_metrics

        return results
    except Exception as exc:
        raise self.retry(exc=exc) from exc
