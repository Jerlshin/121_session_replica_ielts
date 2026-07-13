"""finalize_media (Spec 03 §2.2, §3): stitches a closed session's per-turn
candidate audio into one canonical FLAC file — the long-term scoring
evidence of record (Spec 01 §7). Also records each turn's time offset
within the stitched file ("slice per-turn audio" in the Spec 03 DAG
diagram) so later stages can map a transcript word or a pronunciation
segment back to the turn it came from without re-stitching.

This task only ever reads AudioSegment — VideoSegment is never imported
into this app at all, which is what architecturally guarantees proctoring
video can't leak into the grading pipeline (Spec 01 §3.1, CLAUDE.md rule 3).
"""
import io
import logging
import uuid
import wave

import numpy as np
import soundfile as sf
from sqlalchemy import select

from celery_app import app
from config import settings
from db import session_scope
from job_status import mark_failed, mark_running, mark_succeeded
from models import AudioSegment
from storage import ensure_bucket, get_s3_client

logger = logging.getLogger("worker.tasks.media")

# Spec 01 §4.1 — must match media_tap.py's write contract on the gateway
# side; every audio_segments WAV is this format, guaranteed at capture time.
CANDIDATE_PCM_SAMPLE_RATE_HZ = 16000
CANDIDATE_PCM_SAMPLE_WIDTH_BYTES = 2

TASK_NAME = "finalize_media"


def _extract_pcm16(wav_bytes: bytes) -> bytes:
    with wave.open(io.BytesIO(wav_bytes), "rb") as wav_file:
        return wav_file.readframes(wav_file.getnframes())


@app.task(name="tasks.media.finalize_media", bind=True, max_retries=3, time_limit=300)
def finalize_media(self, session_id: str) -> dict:
    session_uuid = uuid.UUID(session_id)
    mark_running(session_uuid, TASK_NAME)

    try:
        with session_scope() as db:
            segments = list(
                db.scalars(
                    select(AudioSegment)
                    .where(AudioSegment.session_id == session_uuid)
                    .order_by(AudioSegment.created_at)
                )
            )
        if not segments:
            raise RuntimeError(f"no audio_segments found for session={session_id}")

        s3 = get_s3_client()
        pcm_chunks: list[bytes] = []
        turn_boundaries: list[dict] = []
        offset_ms = 0
        for segment in segments:
            obj = s3.get_object(Bucket=settings.s3_bucket, Key=segment.storage_key)
            pcm = _extract_pcm16(obj["Body"].read())
            duration_ms = round(
                len(pcm) / CANDIDATE_PCM_SAMPLE_WIDTH_BYTES / CANDIDATE_PCM_SAMPLE_RATE_HZ * 1000
            )
            turn_boundaries.append(
                {
                    "turn_id": str(segment.turn_id),
                    "start_ms": offset_ms,
                    "end_ms": offset_ms + duration_ms,
                }
            )
            pcm_chunks.append(pcm)
            offset_ms += duration_ms

        combined_pcm = b"".join(pcm_chunks)
        samples = np.frombuffer(combined_pcm, dtype="<i2")

        flac_buffer = io.BytesIO()
        sf.write(
            flac_buffer, samples, CANDIDATE_PCM_SAMPLE_RATE_HZ, format="FLAC", subtype="PCM_16"
        )
        flac_bytes = flac_buffer.getvalue()

        storage_key = f"raw-audio/{session_id}/canonical.flac"
        ensure_bucket(s3)
        s3.put_object(
            Bucket=settings.s3_bucket, Key=storage_key, Body=flac_bytes, ContentType="audio/flac"
        )

        result = {
            "canonical_audio_key": storage_key,
            "turn_boundaries": turn_boundaries,
            "duration_ms": offset_ms,
            "segment_count": len(segments),
        }
        mark_succeeded(session_uuid, TASK_NAME, result)
        return result

    except Exception as exc:
        logger.exception("finalize_media failed session=%s", session_id)
        mark_failed(session_uuid, TASK_NAME, str(exc))
        raise self.retry(exc=exc) from exc
