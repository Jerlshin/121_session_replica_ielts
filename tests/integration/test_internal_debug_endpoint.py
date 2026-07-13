"""GET /internal/sessions/{id}/transcript (Spec 04 §2 Phase 5 exit
criterion): the canonical audio + word-level transcript for a closed
session, visible via an internal debug endpoint gated by a shared token
rather than candidate JWT auth.
"""
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "apps" / "api-gateway"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "apps" / "worker"))

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.services.media_tap import get_s3_client  # noqa: E402
from job_status import mark_succeeded  # noqa: E402
from providers.transcription import TranscriptionProvider, WordResult  # noqa: E402
from tasks.asr import transcribe_full_session  # noqa: E402


class _OneWordProvider(TranscriptionProvider):
    def transcribe(self, audio_bytes: bytes, *, sample_rate: int) -> list[WordResult]:
        return [WordResult(word="testing", start_ms=50, end_ms=300, confidence=0.99, source="fixture")]


def test_debug_endpoint_requires_token_and_returns_pipeline_state():
    with TestClient(app) as client:
        login = client.post(
            "/auth/login", json={"email": "debug-endpoint@example.com", "full_name": "Debug Tester"}
        )
        token = login.json()["access_token"]
        session_id = client.post(
            "/sessions", headers={"Authorization": f"Bearer {token}"}
        ).json()["id"]

        no_token = client.get(f"/internal/sessions/{session_id}/transcript")
        assert no_token.status_code == 401

        wrong_token = client.get(
            f"/internal/sessions/{session_id}/transcript",
            headers={"X-Internal-Debug-Token": "not-the-right-token"},
        )
        assert wrong_token.status_code == 401

        before_pipeline = client.get(
            f"/internal/sessions/{session_id}/transcript",
            headers={"X-Internal-Debug-Token": settings.internal_debug_token},
        )
        assert before_pipeline.status_code == 200
        assert before_pipeline.json()["word_count"] == 0
        assert before_pipeline.json()["canonical_audio_key"] is None

    turn_id = uuid.uuid4()
    canonical_key = f"raw-audio/{session_id}/canonical.flac"
    get_s3_client().put_object(Bucket="ielts-media", Key=canonical_key, Body=b"fixture-not-real-flac")
    mark_succeeded(
        uuid.UUID(session_id),
        "finalize_media",
        {
            "canonical_audio_key": canonical_key,
            "turn_boundaries": [{"turn_id": str(turn_id), "start_ms": 0, "end_ms": 1000}],
            "duration_ms": 1000,
            "segment_count": 1,
        },
    )
    transcribe_full_session(session_id, provider=_OneWordProvider())

    with TestClient(app) as client:
        after_pipeline = client.get(
            f"/internal/sessions/{session_id}/transcript",
            headers={"X-Internal-Debug-Token": settings.internal_debug_token},
        )
        assert after_pipeline.status_code == 200
        body = after_pipeline.json()
        assert body["canonical_audio_key"] == canonical_key
        assert body["word_count"] == 1
        assert body["words"][0] == {
            "turn_id": str(turn_id),
            "seq": 0,
            "word": "testing",
            "start_ms": 50,
            "end_ms": 300,
            "confidence": 0.99,
            "speaker": "candidate",
            "source": "fixture",
        }
        assert body["finalize_media"]["segment_count"] == 1
        assert body["transcribe_full_session"]["word_count"] == 1


def test_debug_endpoint_404s_for_unknown_session():
    with TestClient(app) as client:
        response = client.get(
            f"/internal/sessions/{uuid.uuid4()}/transcript",
            headers={"X-Internal-Debug-Token": settings.internal_debug_token},
        )
        assert response.status_code == 404
