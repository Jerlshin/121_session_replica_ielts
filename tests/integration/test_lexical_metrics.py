"""compute_lexical_metrics golden-file test (Spec 03 §4.2, Spec 04 §2
Phase 6 / §3): asserts a *relative ordering* between a hand-authored
simple/repetitive-vocabulary transcript and a hand-authored diverse/rarer-
vocabulary one — range/ordering based, not exact values (Spec 04 §3).
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
from providers.grammar_check import GrammarCheckProvider  # noqa: E402
from tasks.nlp.lexical import compute_lexical_metrics  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "reference_audio"


class FixtureGrammarCheckProvider(GrammarCheckProvider):
    """Deterministic test double — real LanguageTool needs a JRE + a
    ~200MB server jar, neither available in this sandbox/CI (same posture
    as Phase 5's FixtureTranscriptionProvider)."""

    source_name = "fixture"

    def check(self, text: str) -> list:
        return []


def _create_session() -> str:
    with TestClient(app) as client:
        login = client.post(
            "/auth/login", json={"email": "lexical-golden@example.com", "full_name": "Lexical Tester"}
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
    return compute_lexical_metrics(session_id, provider=FixtureGrammarCheckProvider())


def test_rich_vocabulary_sample_scores_higher_than_simple_sample():
    simple = _run("simple_lexicon_transcript.json")["part3"]
    rich = _run("rich_lexicon_transcript.json")["part3"]

    assert rich["mtld"] > simple["mtld"]
    assert rich["mattr"] > simple["mattr"]
    assert rich["off_top_5000_rarity_ratio"] > simple["off_top_5000_rarity_ratio"]
    assert rich["beyond_b2_ratio"] > simple["beyond_b2_ratio"]
    assert rich["cefr_distribution"]["A1_A2"] < simple["cefr_distribution"]["A1_A2"]

    # Every CEFR band distribution sums to ~1.0 (all words classified).
    for result in (simple, rich):
        assert abs(sum(result["cefr_distribution"].values()) - 1.0) < 1e-6


def test_feature_vector_persisted_with_provenance_for_every_bucket():
    session_id = _create_session()
    _seed_fixture(session_id, "rich_lexicon_transcript.json")
    result = compute_lexical_metrics(session_id, provider=FixtureGrammarCheckProvider())

    assert set(result.keys()) == {"part1", "part2", "part3", "session"}

    with worker_session_scope() as db:
        rows = (
            db.query(FeatureVector)
            .filter_by(session_id=uuid.UUID(session_id), criterion="lexical")
            .all()
        )
        phases = {row.phase for row in rows}
        assert phases == {"part1", "part2", "part3", "session"}
        for row in rows:
            assert row.features["provenance"]["source"] == "fixture"
        part3_row = next(r for r in rows if r.phase == "part3")
        assert part3_row.features["total_words"] == 30
