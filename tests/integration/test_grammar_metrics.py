"""compute_grammar_metrics golden-file test (Spec 03 §4.3, Spec 04 §2
Phase 6 / §3): two independent contrasts, both range/ordering-based, not
exact values (Spec 04 §3) —
1. Structural complexity: a syntactically complex transcript (subordinate
   clauses, passive voice, a relative clause, modals, a conditional)
   against a syntactically simple one (short independent clauses only),
   both checked with a no-error provider so accuracy never confounds the
   structural comparison.
2. Accuracy: the *same* transcript checked against a clean vs. an
   error-injecting FixtureGrammarCheckProvider — real LanguageTool needs a
   JRE + a ~200MB server jar, neither available here (same posture as
   Phase 5's FixtureTranscriptionProvider).
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
from providers.grammar_check import GrammarCheckProvider, GrammarError  # noqa: E402
from tasks.nlp.grammar import compute_grammar_metrics  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "reference_audio"


class FixtureGrammarCheckProvider(GrammarCheckProvider):
    source_name = "fixture"

    def __init__(self, errors: list[GrammarError] | None = None) -> None:
        self._errors = errors or []

    def check(self, text: str) -> list[GrammarError]:
        return list(self._errors)


CANNED_ERRORS = [
    GrammarError("GRAMMAR", "AGREEMENT_RULE", "subject-verb agreement", 0, 5),
    GrammarError("GRAMMAR", "ARTICLE_MISSING", "missing article", 10, 3),
    GrammarError("GRAMMAR", "VERB_TENSE_RULE", "wrong tense", 20, 4),
    GrammarError("TYPOS", "PREP_RULE", "wrong preposition", 30, 2),
    GrammarError("STYLE", "COLLOCATIONS", "awkward phrasing", 40, 6),  # not GRAMMAR/TYPOS — excluded
]


def _create_session() -> str:
    with TestClient(app) as client:
        login = client.post(
            "/auth/login", json={"email": "grammar-golden@example.com", "full_name": "Grammar Tester"}
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


def _run(fixture_name: str, provider: GrammarCheckProvider) -> dict:
    session_id = _create_session()
    _seed_fixture(session_id, fixture_name)
    return compute_grammar_metrics(session_id, provider=provider)


def test_complex_sample_scores_higher_structural_complexity_than_simple():
    no_errors = FixtureGrammarCheckProvider()
    complex_ = _run("complex_grammar_transcript.json", no_errors)["part3"]
    simple = _run("simple_grammar_transcript.json", no_errors)["part3"]

    assert complex_["mean_length_of_t_unit"] > simple["mean_length_of_t_unit"]
    assert complex_["clauses_per_t_unit"] > simple["clauses_per_t_unit"]
    assert complex_["dependent_clause_ratio"] > simple["dependent_clause_ratio"]
    assert (
        complex_["structural_range"]["structural_diversity_count"]
        > simple["structural_range"]["structural_diversity_count"]
    )
    assert complex_["structural_range"]["has_passive_voice"] is True
    assert simple["structural_range"]["has_passive_voice"] is False
    assert complex_["structural_range"]["has_relative_clause"] is True
    assert simple["structural_range"]["has_relative_clause"] is False
    # Both are error-free under a no-error provider — this test isolates
    # structure, not accuracy.
    assert complex_["error_free_clause_ratio"] == 1.0
    assert simple["error_free_clause_ratio"] == 1.0


def test_error_injecting_provider_lowers_accuracy_on_the_same_transcript():
    clean = _run("complex_grammar_transcript.json", FixtureGrammarCheckProvider())["part3"]
    error_prone = _run(
        "complex_grammar_transcript.json", FixtureGrammarCheckProvider(CANNED_ERRORS)
    )["part3"]

    assert clean["grammar_error_count"] == 0
    assert clean["error_free_clause_ratio"] == 1.0
    # 4 of the 5 canned errors are GRAMMAR/TYPOS category — the STYLE one
    # is lexical.py's domain and must be excluded here.
    assert error_prone["grammar_error_count"] == 4
    assert error_prone["error_free_clause_ratio"] < clean["error_free_clause_ratio"]
    assert error_prone["errors_per_100_words"] > clean["errors_per_100_words"]
    taxonomy = error_prone["error_type_taxonomy"]
    assert taxonomy.get("subject_verb_agreement") == 1
    assert taxonomy.get("article") == 1
    assert taxonomy.get("tense") == 1
    assert taxonomy.get("preposition") == 1


def test_feature_vector_persisted_with_provenance_for_every_bucket():
    session_id = _create_session()
    _seed_fixture(session_id, "complex_grammar_transcript.json")
    result = compute_grammar_metrics(session_id, provider=FixtureGrammarCheckProvider(CANNED_ERRORS))

    assert set(result.keys()) == {"part1", "part2", "part3", "session"}

    with worker_session_scope() as db:
        rows = (
            db.query(FeatureVector)
            .filter_by(session_id=uuid.UUID(session_id), criterion="grammar")
            .all()
        )
        phases = {row.phase for row in rows}
        assert phases == {"part1", "part2", "part3", "session"}
        for row in rows:
            assert row.features["provenance"]["source"] == "fixture"
        part3_row = next(r for r in rows if r.phase == "part3")
        assert part3_row.features["t_unit_count"] == 1
