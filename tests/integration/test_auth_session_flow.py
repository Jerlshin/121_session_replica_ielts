"""Exercises the Phase 0 exit criterion end-to-end: a candidate can log in
and create a session row. Requires the dev Postgres stack to be running
(infra/docker/docker-compose.dev.yml) with migrations applied.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "apps" / "api-gateway"))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def test_candidate_can_login_and_create_session():
    # TestClient must be used as a context manager so every request in
    # this test shares one event loop — the async SQLAlchemy engine's
    # connection pool is bound to whichever loop opened it.
    with TestClient(app) as client:
        login_response = client.post(
            "/auth/login", json={"email": "candidate@example.com", "full_name": "Ada Lovelace"}
        )
        assert login_response.status_code == 200
        token = login_response.json()["access_token"]

        session_response = client.post("/sessions", headers={"Authorization": f"Bearer {token}"})
        assert session_response.status_code == 201
        body = session_response.json()
        assert body["status"] == "CREATED"
        assert body["current_phase"] is None
        assert body["resume_token"]

        fetch_response = client.get(
            f"/sessions/{body['id']}", headers={"Authorization": f"Bearer {token}"}
        )
        assert fetch_response.status_code == 200
        assert fetch_response.json()["id"] == body["id"]
