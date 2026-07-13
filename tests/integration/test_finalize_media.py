"""finalize_media (Spec 03 §2.2, §3; Spec 04 §2 Phase 5) against real
Postgres + MinIO: seeds AudioSegment rows + their WAVs, calls the task
directly (bypassing the broker — same pattern the FSM's fixture-driven
tests already use for Gemini), and asserts the canonical FLAC round-trips
to the exact concatenated PCM with correct turn_boundaries.
"""
import io
import struct
import sys
import uuid
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "apps" / "api-gateway"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "apps" / "worker"))

import soundfile as sf  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services.media_tap import get_s3_client  # noqa: E402
from db import session_scope as worker_session_scope  # noqa: E402
from models import AudioSegment, GradingJob  # noqa: E402
from tasks.media import CANDIDATE_PCM_SAMPLE_RATE_HZ, finalize_media  # noqa: E402


def _wav_bytes(num_samples: int, amplitude: int = 2000) -> tuple[bytes, bytes]:
    samples = [int(amplitude * ((i % 32) / 32.0 - 0.5)) for i in range(num_samples)]
    pcm = struct.pack(f"<{num_samples}h", *samples)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(CANDIDATE_PCM_SAMPLE_RATE_HZ)
        wav_file.writeframes(pcm)
    return buf.getvalue(), pcm


def test_finalize_media_stitches_turns_into_canonical_flac():
    with TestClient(app) as client:
        login = client.post(
            "/auth/login", json={"email": "finalize-media@example.com", "full_name": "Media Tester"}
        )
        token = login.json()["access_token"]
        session_id = client.post(
            "/sessions", headers={"Authorization": f"Bearer {token}"}
        ).json()["id"]

    s3 = get_s3_client()
    turn_ids = [uuid.uuid4() for _ in range(3)]
    expected_pcm = b""
    with worker_session_scope() as db:
        for i, turn_id in enumerate(turn_ids):
            wav_bytes, pcm = _wav_bytes(num_samples=1600 + i * 320)
            expected_pcm += pcm
            key = f"raw-audio/{session_id}/segments/{turn_id}_1.wav"
            s3.put_object(Bucket="ielts-media", Key=key, Body=wav_bytes, ContentType="audio/wav")
            db.add(
                AudioSegment(
                    session_id=uuid.UUID(session_id),
                    turn_id=turn_id,
                    seq=1,
                    storage_key=key,
                    checksum="test-checksum",
                    byte_size=len(wav_bytes),
                )
            )

    result = finalize_media(session_id)

    assert result["canonical_audio_key"] == f"raw-audio/{session_id}/canonical.flac"
    assert result["segment_count"] == 3
    assert len(result["turn_boundaries"]) == 3
    assert [b["turn_id"] for b in result["turn_boundaries"]] == [str(t) for t in turn_ids]
    # Boundaries are contiguous and non-overlapping, in stitch order.
    for prev, nxt in zip(result["turn_boundaries"], result["turn_boundaries"][1:]):
        assert prev["end_ms"] == nxt["start_ms"]
    assert result["turn_boundaries"][-1]["end_ms"] == result["duration_ms"]

    obj = s3.get_object(Bucket="ielts-media", Key=result["canonical_audio_key"])
    data, sample_rate = sf.read(io.BytesIO(obj["Body"].read()), dtype="int16")
    assert sample_rate == CANDIDATE_PCM_SAMPLE_RATE_HZ
    assert data.tobytes() == expected_pcm

    with worker_session_scope() as db:
        job = (
            db.query(GradingJob)
            .filter_by(session_id=uuid.UUID(session_id), task_name="finalize_media")
            .one()
        )
        assert job.status == "SUCCEEDED"
        assert job.attempt == 1
        assert job.error is None
        assert job.result["canonical_audio_key"] == result["canonical_audio_key"]
