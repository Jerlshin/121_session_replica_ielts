"""transcribe_full_session (Spec 03 §2.2, §3): authoritative BATCH
re-transcription of finalize_media's canonical.flac — materially more
accurate than the live caption lane (Spec 01 §4.3) and the sole source of
truth for grading. Reads finalize_media's grading_jobs row for the
canonical audio key + turn boundaries rather than a chain-passed argument
(see pipelines/grading_pipeline.py's docstring) so this task supports a
targeted solo re-run independent of the chain.
"""
import logging
import uuid

from sqlalchemy import delete

from celery_app import app
from config import settings
from db import session_scope
from job_status import load_result, mark_failed, mark_running, mark_succeeded
from models import Transcript
from providers.transcription import TranscriptionProvider, WordResult, transcribe_with_fallback
from storage import get_s3_client
from tasks.media import CANDIDATE_PCM_SAMPLE_RATE_HZ

logger = logging.getLogger("worker.tasks.asr")

TASK_NAME = "transcribe_full_session"
FINALIZE_MEDIA_TASK_NAME = "finalize_media"

# Gemini's audio is relayed to the client for playback but never persisted
# server-side (only the candidate's own PCM is tapped, Spec 01 §4.2) — the
# canonical audio this task transcribes is candidate-only.
SPEAKER = "candidate"


def _turn_id_for_offset(turn_boundaries: list[dict], start_ms: int) -> str:
    """Finds which turn interval a word's start_ms falls into. Falls back
    to the nearest turn (clamped) rather than raising, since stitching/ASR
    rounding can occasionally place a boundary word a few ms outside its
    interval."""
    if not turn_boundaries:
        raise RuntimeError("no turn_boundaries to attribute words to")
    for turn in turn_boundaries:
        if turn["start_ms"] <= start_ms < turn["end_ms"]:
            return turn["turn_id"]
    if start_ms < turn_boundaries[0]["start_ms"]:
        return turn_boundaries[0]["turn_id"]
    return turn_boundaries[-1]["turn_id"]


def _persist_words(
    session_id: uuid.UUID, turn_boundaries: list[dict], words: list[WordResult]
) -> None:
    """Delete-then-insert, in one transaction — a re-run's word *count* can
    legitimately differ from the previous run's (it's a different
    transcription, not a correction to fixed slots), so per-word upsert
    keyed on a re-derived `seq` can't guarantee idempotency: a shorter
    re-run would leave the previous run's trailing words stranded. A full
    replace is what actually satisfies Spec 03 §2.4's "outputs are
    upserted, not appended" for a table shaped like this one.
    """
    seq_by_turn: dict[str, int] = {}
    rows = []
    for word in words:
        turn_id = _turn_id_for_offset(turn_boundaries, word.start_ms)
        seq = seq_by_turn.get(turn_id, 0)
        seq_by_turn[turn_id] = seq + 1
        rows.append(
            Transcript(
                session_id=session_id,
                turn_id=uuid.UUID(turn_id),
                seq=seq,
                word=word.word,
                start_ms=word.start_ms,
                end_ms=word.end_ms,
                confidence=word.confidence,
                speaker=SPEAKER,
                source=word.source,
            )
        )

    with session_scope() as db:
        db.execute(delete(Transcript).where(Transcript.session_id == session_id))
        db.add_all(rows)


@app.task(name="tasks.asr.transcribe_full_session", bind=True, max_retries=3, time_limit=600)
def transcribe_full_session(
    self, session_id: str, *, provider: TranscriptionProvider | None = None
) -> dict:
    session_uuid = uuid.UUID(session_id)
    mark_running(session_uuid, TASK_NAME)

    try:
        finalize_result = load_result(session_uuid, FINALIZE_MEDIA_TASK_NAME)
        if not finalize_result:
            raise RuntimeError(
                f"finalize_media has not succeeded for session={session_id} — "
                "transcribe_full_session cannot run without a canonical audio file"
            )
        canonical_key = finalize_result["canonical_audio_key"]
        turn_boundaries = finalize_result["turn_boundaries"]

        s3 = get_s3_client()
        obj = s3.get_object(Bucket=settings.s3_bucket, Key=canonical_key)
        audio_bytes = obj["Body"].read()

        transcribe = provider.transcribe if provider is not None else transcribe_with_fallback
        words = transcribe(audio_bytes, sample_rate=CANDIDATE_PCM_SAMPLE_RATE_HZ)

        _persist_words(session_uuid, turn_boundaries, words)

        result = {"word_count": len(words), "canonical_audio_key": canonical_key}
        mark_succeeded(session_uuid, TASK_NAME, result)
        return result

    except Exception as exc:
        logger.exception("transcribe_full_session failed session=%s", session_id)
        mark_failed(session_uuid, TASK_NAME, str(exc))
        raise self.retry(exc=exc) from exc
