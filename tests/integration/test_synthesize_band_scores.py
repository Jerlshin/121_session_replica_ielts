"""synthesize_band_scores integration test (Spec 03 §2.2, §5, §5.6) — real
Postgres, real feature_vectors/transcripts rows, a FixtureScoringLLM
standing in for Claude (same posture as Phase 5/6's fixture providers —
real vendor code stays behind ANTHROPIC_API_KEY and is never exercised in
CI). Proves: consistent feature-derived judge passes produce a persisted
band_score_reports row with flag_for_human_review=False, while conflicting
passes on a single criterion trip the human-review flag; both cases carry
the complete audit trail (JudgeInput + both raw passes + reconciliation).
"""
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "apps" / "api-gateway"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "apps" / "worker"))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from config import settings  # noqa: E402
from db import session_scope as worker_session_scope  # noqa: E402
from feature_vectors import write_feature_vector  # noqa: E402
from models import AudioSegment, BandScoreReport, Transcript  # noqa: E402
from providers.scoring_llm import CriterionScore, JudgeInput, JudgeOutput  # noqa: E402
from tasks.scoring import synthesize_band_scores  # noqa: E402

RUBRIC_ASSETS_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "rubric_assets"

CRITERIA = (
    "fluency_coherence",
    "lexical_resource",
    "grammatical_range_accuracy",
    "pronunciation",
)


class FixtureScoringLLM:
    """Test-only ScoringLLM (Spec 03 §5.2's swappable interface) — returns
    one canned JudgeOutput per call, in call order, so a test can script
    exactly what "pass 1" and "pass 2" each produce."""

    source_name = "fixture"

    def __init__(self, bands_by_pass: list[dict[str, float]]) -> None:
        self._bands_by_pass = list(bands_by_pass)
        self.call_count = 0

    def score(self, judge_input: JudgeInput) -> JudgeOutput:
        bands = self._bands_by_pass[self.call_count]
        self.call_count += 1
        return JudgeOutput(
            session_id=judge_input.session_id,
            criterion_scores=[
                CriterionScore(
                    criterion=criterion,
                    band=bands[criterion],
                    justification=f"References MLR=4.2 for {criterion}.",
                    evidence_features=["MLR=4.2"],
                    confidence=0.85,
                )
                for criterion in CRITERIA
            ],
            overall_band=sum(bands.values()) / len(bands),
            flags=[],
        )


def _create_session(email: str) -> str:
    with TestClient(app) as client:
        login = client.post("/auth/login", json={"email": email, "full_name": "Scoring Tester"})
        token = login.json()["access_token"]
        return client.post("/sessions", headers={"Authorization": f"Bearer {token}"}).json()["id"]


def _seed_transcript_and_features(session_id: str) -> None:
    session_uuid = uuid.UUID(session_id)
    with worker_session_scope() as db:
        turn_id = uuid.uuid4()
        db.add(
            AudioSegment(
                session_id=session_uuid,
                turn_id=turn_id,
                seq=1,
                storage_key=f"raw-audio/{session_id}/segments/{turn_id}_1.wav",
                checksum="fixture-checksum",
                byte_size=0,
                exam_phase="PART3_DISCUSSION",
            )
        )
        words = ["I", "think", "that", "technology", "has", "changed", "society"]
        for i, word in enumerate(words):
            db.add(
                Transcript(
                    session_id=session_uuid,
                    turn_id=turn_id,
                    seq=i,
                    word=word,
                    start_ms=i * 500,
                    end_ms=i * 500 + 400,
                    confidence=0.95,
                    speaker="candidate",
                    source="fixture",
                )
            )

    for criterion in ("fluency", "lexical", "grammar", "pronunciation"):
        for phase in ("part3", "session"):
            write_feature_vector(
                session_uuid, criterion, phase, {"stub": True, "provenance": {"source": "fixture"}}
            )


def _run(email: str, bands_by_pass: list[dict[str, float]], monkeypatch) -> tuple[str, dict]:
    monkeypatch.setattr(settings, "rubric_assets_dir", RUBRIC_ASSETS_DIR)
    session_id = _create_session(email)
    _seed_transcript_and_features(session_id)
    llm = FixtureScoringLLM(bands_by_pass)
    result = synthesize_band_scores(session_id, scoring_llm=llm)
    return session_id, result


def test_consistent_passes_persist_report_without_human_review_flag(monkeypatch):
    agree = {c: 6.5 for c in CRITERIA}
    session_id, result = _run("scoring-agree@example.com", [agree, agree], monkeypatch)

    assert result["flag_for_human_review"] is False
    assert result["overall_band"] == 6.5

    with worker_session_scope() as db:
        row = db.query(BandScoreReport).filter_by(session_id=uuid.UUID(session_id)).one()
        assert row.flag_for_human_review is False
        assert row.overall_band == 6.5
        assert row.judge_pass_1 is not None
        assert row.judge_pass_2 is not None
        assert row.judge_input["feature_status"] == {c: "ok" for c in CRITERIA}
        assert {s["criterion"] for s in row.criterion_scores} == set(CRITERIA)


def test_conflicting_pronunciation_pass_flags_for_human_review(monkeypatch):
    pass_1_bands = {c: 6.0 for c in CRITERIA}
    pass_2_bands = dict(pass_1_bands)
    pass_2_bands["pronunciation"] = 8.0

    session_id, result = _run(
        "scoring-disagree@example.com", [pass_1_bands, pass_2_bands], monkeypatch
    )

    assert result["flag_for_human_review"] is True

    with worker_session_scope() as db:
        row = db.query(BandScoreReport).filter_by(session_id=uuid.UUID(session_id)).one()
        assert row.flag_for_human_review is True
        assert row.reconciliation["flagged_criteria"] == ["pronunciation"]
        assert row.reconciliation["band_diffs"]["pronunciation"] == 2.0
        # Full audit trail persisted regardless of the flag (Spec 03 §5.6).
        assert row.judge_pass_1["criterion_scores"] is not None
        assert row.judge_pass_2["criterion_scores"] is not None


def test_rerun_upserts_rather_than_duplicates_the_report(monkeypatch):
    agree = {c: 6.0 for c in CRITERIA}
    session_id, _ = _run("scoring-rerun@example.com", [agree, agree], monkeypatch)

    monkeypatch.setattr(settings, "rubric_assets_dir", RUBRIC_ASSETS_DIR)
    disagree = dict(agree)
    disagree_pass_2 = dict(agree)
    disagree_pass_2["pronunciation"] = 8.0
    llm = FixtureScoringLLM([disagree, disagree_pass_2])
    synthesize_band_scores(session_id, scoring_llm=llm)

    with worker_session_scope() as db:
        rows = db.query(BandScoreReport).filter_by(session_id=uuid.UUID(session_id)).all()
        assert len(rows) == 1
        assert rows[0].flag_for_human_review is True
