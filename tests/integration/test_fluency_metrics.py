"""compute_fluency_metrics golden-file test (Spec 03 §4.1, Spec 04 §2
Phase 6 / §3): asserts a *relative ordering* of metrics between a
hand-authored fluent transcript and a hand-authored hesitant one, not
exact values — "pin ranges, not exact values... the tests exist to catch
gross regressions (a fluent sample suddenly scoring as hesitant), not to
freeze the pipeline against any change at all" (Spec 04 §3). No real
recorded audio is needed for this task — it operates on transcript word
timestamps, not raw audio.
"""
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "apps" / "api-gateway"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "apps" / "worker"))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from db import session_scope as worker_session_scope  # noqa: E402
from models import AudioSegment, FeatureVector, Transcript  # noqa: E402
from tasks.nlp.fluency import compute_fluency_metrics  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "reference_audio"


def _create_session() -> str:
    with TestClient(app) as client:
        login = client.post(
            "/auth/login", json={"email": "fluency-golden@example.com", "full_name": "Fluency Tester"}
        )
        token = login.json()["access_token"]
        return client.post("/sessions", headers={"Authorization": f"Bearer {token}"}).json()["id"]


def _seed_fixture(session_id: str, fixture_name: str) -> None:
    fixture = json.loads((FIXTURES_DIR / fixture_name).read_text())
    session_uuid = uuid.UUID(session_id)

    with worker_session_scope() as db:
        for turn in fixture["turns"]:
            turn_id = uuid.uuid4()
            db.add(
                AudioSegment(
                    session_id=session_uuid,
                    turn_id=turn_id,
                    seq=1,
                    storage_key=f"raw-audio/{session_id}/segments/{turn_id}_1.wav",
                    checksum="fixture-checksum",
                    byte_size=0,
                    exam_phase=fixture["exam_phase"],
                )
            )
            for i, word in enumerate(turn["words"]):
                db.add(
                    Transcript(
                        session_id=session_uuid,
                        turn_id=turn_id,
                        seq=i,
                        word=word["word"],
                        start_ms=word["start_ms"],
                        end_ms=word["end_ms"],
                        confidence=word["confidence"],
                        speaker="candidate",
                        source="fixture",
                    )
                )


def _run(fixture_name: str) -> dict:
    session_id = _create_session()
    _seed_fixture(session_id, fixture_name)
    return compute_fluency_metrics(session_id)


def test_fluent_sample_scores_better_than_hesitant_sample():
    fluent = _run("fluent_transcript.json")["part2"]
    hesitant = _run("hesitant_transcript.json")["part2"]

    assert fluent["speech_rate_wpm"] > hesitant["speech_rate_wpm"]
    assert fluent["articulation_rate_syll_per_s"] > hesitant["articulation_rate_syll_per_s"]
    assert fluent["phonation_time_ratio"] > hesitant["phonation_time_ratio"]
    assert fluent["mean_length_of_run"] > hesitant["mean_length_of_run"]
    assert fluent["silent_pause_rate_per_100_words"] < hesitant["silent_pause_rate_per_100_words"]
    assert fluent["filled_pause_rate_per_100_words"] == 0.0
    assert hesitant["filled_pause_rate_per_100_words"] > 0.0
    assert fluent["self_repair_rate_per_100_words"] == 0.0
    assert hesitant["self_repair_rate_per_100_words"] > 0.0

    placement = hesitant["pause_placement"]
    assert placement["clause_boundary"] + placement["mid_clause"] > 0


def test_feature_vector_persisted_with_provenance_for_every_bucket():
    session_id = _create_session()
    _seed_fixture(session_id, "hesitant_transcript.json")
    result = compute_fluency_metrics(session_id)

    assert set(result.keys()) == {"part1", "part2", "part3", "session"}

    with worker_session_scope() as db:
        rows = (
            db.query(FeatureVector)
            .filter_by(session_id=uuid.UUID(session_id), criterion="fluency")
            .all()
        )
        phases = {row.phase for row in rows}
        assert phases == {"part1", "part2", "part3", "session"}
        for row in rows:
            assert row.features["provenance"]["source"] == "rule_based"
        # part1/part3 had no words in this fixture — empty but well-formed.
        part1_row = next(r for r in rows if r.phase == "part1")
        assert part1_row.features["total_words"] == 0
        part2_row = next(r for r in rows if r.phase == "part2")
        assert part2_row.features["total_words"] == 17
