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
SWEEP_TASK_NAME = "sweep_expired_raw_audio"


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


def _delete_raw_audio_segments(s3, session_id: str) -> dict:
    """The actual deletion logic, pulled out of the Celery task so it's
    directly unit-testable against a fake S3 client with no DB/broker
    involved — the task wrapper only adds job-status bookkeeping."""
    prefix = f"raw-audio/{session_id}/segments/"
    deleted_keys: list[str] = []

    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=settings.s3_bucket, Prefix=prefix):
        objects = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
        if not objects:
            continue
        s3.delete_objects(Bucket=settings.s3_bucket, Delete={"Objects": objects})
        deleted_keys.extend(obj["Key"] for obj in objects)

    return {"deleted_count": len(deleted_keys), "prefix": prefix}


@app.task(name="tasks.media.sweep_expired_raw_audio", bind=True, max_retries=3, time_limit=120)
def sweep_expired_raw_audio(self, session_id: str) -> dict:
    """Data-minimization sweep (Spec 01 §7/§8, Spec 04 §2 Phase 8): chained
    as the final immutable step in grading_pipeline.py's DAG, firing only
    once `synthesize_band_scores` has succeeded. By then finalize_media has
    already stitched every per-turn candidate recording into
    canonical.flac — Spec 01 §7 scopes raw per-turn segments to "session
    lifetime + grading buffer" (not long-term) while canonical.flac is the
    long-term scoring evidence of record, so the per-turn WAVs have served
    their purpose and are deleted. Only the S3 objects (the actual
    sensitive audio payload) are removed; the `audio_segments` DB rows
    (checksum/byte_size/timestamps) are kept as an audit trail of what
    existed and when.
    """
    session_uuid = uuid.UUID(session_id)
    mark_running(session_uuid, SWEEP_TASK_NAME)

    try:
        result = _delete_raw_audio_segments(get_s3_client(), session_id)
        mark_succeeded(session_uuid, SWEEP_TASK_NAME, result)
        return result

    except Exception as exc:
        logger.exception("sweep_expired_raw_audio failed session=%s", session_id)
        mark_failed(session_uuid, SWEEP_TASK_NAME, str(exc))
        raise self.retry(exc=exc) from exc
