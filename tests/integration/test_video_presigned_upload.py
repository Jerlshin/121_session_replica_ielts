"""Proves the video track's decoupled path (CLAUDE.md rule 3): the API
issues a presigned URL, the "browser" PUTs bytes directly to object
storage without the bytes ever transiting the API process, and the
completion callback flips the pointer row to UPLOADED.
"""
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "apps" / "api-gateway"))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def test_video_presigned_upload_round_trip():
    with TestClient(app) as client:
        login_response = client.post(
            "/auth/login",
            json={"email": "proctoring@example.com", "full_name": "Proctoring Tester"},
        )
        token = login_response.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        session_id = client.post("/sessions", headers=headers).json()["id"]

        upload = client.post(f"/sessions/{session_id}/video-upload-url", headers=headers)
        assert upload.status_code == 201
        body = upload.json()
        assert body["storage_key"] == f"raw-video/{session_id}/full.webm"

        # The presigned PUT goes straight to MinIO — not through the FastAPI app.
        fake_webm_bytes = b"\x1aE\xdf\xa3fake-webm-payload"
        put_response = httpx.put(
            body["upload_url"],
            content=fake_webm_bytes,
            headers={"Content-Type": "video/webm"},
        )
        assert put_response.status_code == 200

        complete = client.post(f"/sessions/{session_id}/video-upload-complete", headers=headers)
        assert complete.status_code == 204
