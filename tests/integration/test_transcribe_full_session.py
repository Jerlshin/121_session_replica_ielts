"""transcribe_full_session (Spec 03 §2.2, §3; Spec 04 §2 Phase 5) against
real Postgres + MinIO: seeds a finalize_media grading_jobs result (canonical
audio key + turn_boundaries), injects a deterministic FixtureTranscriptionProvider
(neither Deepgram nor WhisperX can be exercised in this environment — no API
key, no ML models), and asserts correct per-turn word attribution and
idempotent re-run (Spec 03 §2.4's upsert-not-append contract).
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
from job_status import mark_succeeded  # noqa: E402
from models import Transcript  # noqa: E402
from providers.transcription import TranscriptionProvider, WordResult  # noqa: E402
from tasks.asr import transcribe_full_session  # noqa: E402


class FixtureTranscriptionProvider(TranscriptionProvider):
    """Deterministic test double — mirrors _fake_gemini_live_server.py's
    role for the ASR vendor boundary. Ignores audio bytes entirely; the
    task under test never inspects them beyond downloading and forwarding."""

    def __init__(self, words: list[WordResult]) -> None:
        self._words = words

    def transcribe(self, audio_bytes: bytes, *, sample_rate: int) -> list[WordResult]:
        return self._words


def _create_session() -> str:
    with TestClient(app) as client:
        login = client.post(
            "/auth/login", json={"email": "transcribe@example.com", "full_name": "ASR Tester"}
        )
        token = login.json()["access_token"]
        return client.post("/sessions", headers={"Authorization": f"Bearer {token}"}).json()["id"]


def test_transcribe_full_session_attributes_words_to_turns_and_is_idempotent():
    session_id = _create_session()
    session_uuid = uuid.UUID(session_id)
    turn_a, turn_b = uuid.uuid4(), uuid.uuid4()

    s3 = get_s3_client()
    canonical_key = f"raw-audio/{session_id}/canonical.flac"
    s3.put_object(Bucket="ielts-media", Key=canonical_key, Body=b"fixture-audio-not-real-flac")

    turn_boundaries = [
        {"turn_id": str(turn_a), "start_ms": 0, "end_ms": 1000},
        {"turn_id": str(turn_b), "start_ms": 1000, "end_ms": 2000},
    ]
    mark_succeeded(
        session_uuid,
        "finalize_media",
        {
            "canonical_audio_key": canonical_key,
            "turn_boundaries": turn_boundaries,
            "duration_ms": 2000,
            "segment_count": 2,
        },
    )

    provider = FixtureTranscriptionProvider(
        [
            WordResult(word="hello", start_ms=100, end_ms=300, confidence=0.95, source="fixture"),
            WordResult(word="world", start_ms=300, end_ms=600, confidence=0.9, source="fixture"),
            WordResult(word="again", start_ms=1200, end_ms=1500, confidence=0.8, source="fixture"),
        ]
    )

    result = transcribe_full_session(session_id, provider=provider)
    assert result["word_count"] == 3
    assert result["canonical_audio_key"] == canonical_key

    with worker_session_scope() as db:
        rows = (
            db.query(Transcript)
            .filter_by(session_id=session_uuid)
            .order_by(Transcript.start_ms)
            .all()
        )
        assert len(rows) == 3
        assert (str(rows[0].turn_id), rows[0].seq, rows[0].word) == (str(turn_a), 0, "hello")
        assert (str(rows[1].turn_id), rows[1].seq, rows[1].word) == (str(turn_a), 1, "world")
        assert (str(rows[2].turn_id), rows[2].seq, rows[2].word) == (str(turn_b), 0, "again")
        assert all(r.speaker == "candidate" and r.source == "fixture" for r in rows)

    # Re-run with different content at the same offsets — upsert, not append.
    provider_v2 = FixtureTranscriptionProvider(
        [WordResult(word="hi", start_ms=100, end_ms=200, confidence=0.99, source="fixture")]
    )
    transcribe_full_session(session_id, provider=provider_v2)

    with worker_session_scope() as db:
        rows = db.query(Transcript).filter_by(session_id=session_uuid).all()
        assert len(rows) == 1, "re-run must upsert on (session_id, turn_id, seq), not append"
        assert rows[0].word == "hi"


def test_transcribe_full_session_fails_without_finalize_media_result():
    session_id = _create_session()
    result = None
    try:
        result = transcribe_full_session(session_id, provider=FixtureTranscriptionProvider([]))
    except Exception:
        pass
    assert result is None, "must not succeed when finalize_media has not run yet"

    with worker_session_scope() as db:
        from models import GradingJob

        job = (
            db.query(GradingJob)
            .filter_by(session_id=uuid.UUID(session_id), task_name="transcribe_full_session")
            .one()
        )
        assert job.status == "FAILED"
        assert job.error is not None
