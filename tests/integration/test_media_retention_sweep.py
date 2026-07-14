"""sweep_expired_raw_audio (Spec 01 §7/§8, Spec 04 §2 Phase 8) against real
Postgres + MinIO: seeds per-turn segment objects plus a canonical.flac,
runs the task directly (bypassing the broker, same pattern as
test_finalize_media.py), and asserts only the segments/ prefix is deleted
while canonical.flac and the audio_segments DB rows (the audit trail)
survive untouched.
"""
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "apps" / "api-gateway"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "apps" / "worker"))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services.media_tap import get_s3_client  # noqa: E402
from db import session_scope as worker_session_scope  # noqa: E402
from models import AudioSegment, GradingJob  # noqa: E402
from tasks.media import sweep_expired_raw_audio  # noqa: E402


def _create_session(email: str) -> str:
    with TestClient(app) as client:
        login = client.post("/auth/login", json={"email": email, "full_name": "Retention Tester"})
        token = login.json()["access_token"]
        return client.post("/sessions", headers={"Authorization": f"Bearer {token}"}).json()["id"]


def test_sweep_deletes_segments_but_keeps_canonical_flac_and_db_rows():
    session_id = _create_session("retention-sweep@example.com")
    s3 = get_s3_client()

    segment_keys = []
    with worker_session_scope() as db:
        for i in range(2):
            turn_id = uuid.uuid4()
            key = f"raw-audio/{session_id}/segments/{turn_id}_1.wav"
            s3.put_object(Bucket="ielts-media", Key=key, Body=b"fixture-wav-bytes", ContentType="audio/wav")
            segment_keys.append(key)
            db.add(
                AudioSegment(
                    session_id=uuid.UUID(session_id),
                    turn_id=turn_id,
                    seq=1,
                    storage_key=key,
                    checksum="test-checksum",
                    byte_size=17,
                )
            )

    canonical_key = f"raw-audio/{session_id}/canonical.flac"
    s3.put_object(Bucket="ielts-media", Key=canonical_key, Body=b"fixture-flac-bytes", ContentType="audio/flac")

    result = sweep_expired_raw_audio(session_id)

    assert result["deleted_count"] == 2
    assert result["prefix"] == f"raw-audio/{session_id}/segments/"

    for key in segment_keys:
        assert not _object_exists(s3, key)
    assert _object_exists(s3, canonical_key)

    with worker_session_scope() as db:
        assert db.query(AudioSegment).filter_by(session_id=uuid.UUID(session_id)).count() == 2

        job = (
            db.query(GradingJob)
            .filter_by(session_id=uuid.UUID(session_id), task_name="sweep_expired_raw_audio")
            .one()
        )
        assert job.status == "SUCCEEDED"
        assert job.result["deleted_count"] == 2


def test_sweep_is_a_no_op_when_no_raw_segments_exist():
    session_id = _create_session("retention-sweep-empty@example.com")

    result = sweep_expired_raw_audio(session_id)

    assert result["deleted_count"] == 0


def _object_exists(s3, key: str) -> bool:
    from botocore.exceptions import ClientError

    try:
        s3.head_object(Bucket="ielts-media", Key=key)
        return True
    except ClientError:
        return False
