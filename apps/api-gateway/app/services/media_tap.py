import hashlib
import io
import uuid
import wave

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import ClientError

from app.config import settings

# Client -> Gemini audio contract (Spec 01 §4.1): 16-bit PCM, mono, 16kHz.
# Phase 1 has no Gemini in the loop yet, but the wire format the browser
# captures and the gateway stores is the same one Phase 2 will bridge.
PCM_SAMPLE_RATE_HZ = 16000
PCM_SAMPLE_WIDTH_BYTES = 2
PCM_CHANNELS = 1


def get_s3_client():
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
        config=BotoConfig(signature_version="s3v4"),
    )


def ensure_bucket(client=None) -> None:
    client = client or get_s3_client()
    try:
        client.head_bucket(Bucket=settings.s3_bucket)
    except ClientError:
        client.create_bucket(Bucket=settings.s3_bucket)


def _wrap_pcm16_as_wav(pcm_bytes: bytes) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(PCM_CHANNELS)
        wav_file.setsampwidth(PCM_SAMPLE_WIDTH_BYTES)
        wav_file.setframerate(PCM_SAMPLE_RATE_HZ)
        wav_file.writeframes(pcm_bytes)
    return buffer.getvalue()


def persist_turn_audio(
    session_id: uuid.UUID, turn_id: uuid.UUID, seq: int, pcm_bytes: bytes
) -> dict:
    """Wraps a completed turn's raw PCM16 buffer as WAV and uploads it to
    object storage at the canonical key (Spec 01 §7): one flush per turn,
    matching the gateway's rolling-buffer-then-flush sequence (§4.2) rather
    than a write per 20ms frame. Runs synchronously — called via
    run_in_threadpool from the async WS handler since flushes happen once
    per turn, not once per frame.
    """
    wav_bytes = _wrap_pcm16_as_wav(pcm_bytes)
    checksum = hashlib.sha256(wav_bytes).hexdigest()
    storage_key = f"raw-audio/{session_id}/segments/{turn_id}_{seq}.wav"

    client = get_s3_client()
    ensure_bucket(client)
    client.put_object(
        Bucket=settings.s3_bucket,
        Key=storage_key,
        Body=wav_bytes,
        ContentType="audio/wav",
    )
    return {"storage_key": storage_key, "checksum": checksum, "byte_size": len(wav_bytes)}
