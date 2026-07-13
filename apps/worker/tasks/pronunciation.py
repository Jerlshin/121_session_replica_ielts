"""compute_pronunciation_scores (Spec 03 §2.2, §4.4): confidence-gated
Azure/GOP fallback per candidate turn (unscripted-mode assessment — the
backline ASR transcript stands in as the reference text), plus a real,
always-computed prosody proxy via librosa. Reads each turn's **raw
per-turn WAV** from the media spine (Spec 01 §7) — not canonical.flac —
since finalize_media already produced exactly these files and no
re-slicing is needed. Computed per phase (part1/part2/part3) and as a
session aggregate, written to feature_vectors with per-segment `source`
provenance (Spec 03 §4.4's explicit requirement).
"""
import uuid

from sqlalchemy import select

from celery_app import app
from config import settings
from db import session_scope
from feature_vectors import write_feature_vector
from models import AudioSegment, Transcript
from nlp_common import PHASE_BUCKET_ORDER
from providers.pronunciation import PronunciationProvider, assess_with_fallback, prosody_proxy
from storage import get_s3_client
from tasks.media import CANDIDATE_PCM_SAMPLE_RATE_HZ

TASK_NAME = "compute_pronunciation_scores"

# Same Spec 02 §1 phase bucketing as nlp_common's transcript loader —
# duplicated rather than imported because this task keys off
# AudioSegment.exam_phase directly (it needs the per-turn storage_key,
# which load_words_by_phase's Transcript-only query doesn't carry).
_PHASE_BUCKETS = {
    "PART1_TOPIC_A": "part1",
    "PART1_TOPIC_B": "part1",
    "PART1_TOPIC_C": "part1",
    "PART2_LONG_TURN": "part2",
    "PART2_ROUNDOFF": "part2",
    "PART3_DISCUSSION": "part3",
}


def _load_scoreable_segments(session_id: uuid.UUID) -> list[dict]:
    with session_scope() as db:
        segments = db.scalars(
            select(AudioSegment).where(AudioSegment.session_id == session_id)
        ).all()
        words = db.scalars(
            select(Transcript)
            .where(Transcript.session_id == session_id)
            .order_by(Transcript.seq)
        ).all()

        words_by_turn: dict[uuid.UUID, list[str]] = {}
        for w in words:
            words_by_turn.setdefault(w.turn_id, []).append(w.word)

        scoreable = []
        for seg in segments:
            bucket = _PHASE_BUCKETS.get(seg.exam_phase)
            if bucket is None:
                continue
            scoreable.append(
                {
                    "turn_id": seg.turn_id,
                    "phase": bucket,
                    "storage_key": seg.storage_key,
                    "reference_text": " ".join(words_by_turn.get(seg.turn_id, [])),
                }
            )
    return scoreable


def _score_segment(segment: dict, provider: PronunciationProvider | None) -> dict:
    s3 = get_s3_client()
    obj = s3.get_object(Bucket=settings.s3_bucket, Key=segment["storage_key"])
    audio_bytes = obj["Body"].read()

    prosody = prosody_proxy(audio_bytes, CANDIDATE_PCM_SAMPLE_RATE_HZ)

    if provider is not None:
        result = provider.assess(
            audio_bytes, segment["reference_text"], sample_rate=CANDIDATE_PCM_SAMPLE_RATE_HZ
        )
        source = getattr(provider, "source_name", "fixture")
    else:
        result, source = assess_with_fallback(
            audio_bytes,
            segment["reference_text"],
            sample_rate=CANDIDATE_PCM_SAMPLE_RATE_HZ,
            low_snr_flag=prosody["low_snr_flag"],
        )

    return {
        "turn_id": str(segment["turn_id"]),
        "phase": segment["phase"],
        "accuracy": result.accuracy,
        "fluency": result.fluency,
        "completeness": result.completeness,
        "prosody_vendor_score": result.prosody,
        "pitch_range_hz": prosody["pitch_range_hz"],
        "stress_timing_regularity": prosody["stress_timing_regularity"],
        "confidence": result.confidence,
        "source": source,
    }


def _empty_aggregate() -> dict:
    return {
        "accuracy": 0.0,
        "fluency": 0.0,
        "completeness": 0.0,
        "prosody_vendor_score": 0.0,
        "pitch_range_hz": 0.0,
        "stress_timing_regularity": 0.0,
        "segment_count": 0,
    }


def _aggregate(scores: list[dict]) -> dict:
    if not scores:
        return _empty_aggregate()
    n = len(scores)
    return {
        "accuracy": round(sum(s["accuracy"] for s in scores) / n, 2),
        "fluency": round(sum(s["fluency"] for s in scores) / n, 2),
        "completeness": round(sum(s["completeness"] for s in scores) / n, 2),
        "prosody_vendor_score": round(sum(s["prosody_vendor_score"] for s in scores) / n, 2),
        "pitch_range_hz": round(sum(s["pitch_range_hz"] for s in scores) / n, 2),
        "stress_timing_regularity": round(
            sum(s["stress_timing_regularity"] for s in scores) / n, 2
        ),
        "segment_count": n,
    }


def _provenance(scores: list[dict]) -> dict:
    return {
        "segments": [
            {"turn_id": s["turn_id"], "source": s["source"], "confidence": s["confidence"]}
            for s in scores
        ]
    }


@app.task(
    name="tasks.pronunciation.compute_pronunciation_scores",
    bind=True,
    max_retries=3,
    time_limit=300,
)
def compute_pronunciation_scores(
    self, session_id: str, *, provider: PronunciationProvider | None = None
) -> dict:
    session_uuid = uuid.UUID(session_id)
    try:
        segments = _load_scoreable_segments(session_uuid)
        all_scores = [_score_segment(seg, provider) for seg in segments]

        results = {}
        for phase in PHASE_BUCKET_ORDER:
            phase_scores = [s for s in all_scores if s["phase"] == phase]
            metrics = _aggregate(phase_scores)
            metrics["provenance"] = _provenance(phase_scores)
            write_feature_vector(session_uuid, "pronunciation", phase, metrics)
            results[phase] = metrics

        session_metrics = _aggregate(all_scores)
        session_metrics["provenance"] = _provenance(all_scores)
        write_feature_vector(session_uuid, "pronunciation", "session", session_metrics)
        results["session"] = session_metrics

        return results
    except Exception as exc:
        raise self.retry(exc=exc) from exc
