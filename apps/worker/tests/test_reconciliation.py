"""self_consistency_reconciliation unit tests (Spec 03 §5.6) — pure
function, no I/O, no real Postgres/broker needed. Covers the three cases
that decide `flag_for_human_review`: agreement, a >1.0-band disagreement
on a single criterion, and a criterion one pass omits outright.
"""
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from providers.scoring_llm import CriterionScore, JudgeOutput  # noqa: E402
from reconciliation import self_consistency_reconciliation  # noqa: E402

SESSION_ID = uuid.uuid4()
CRITERIA = (
    "fluency_coherence",
    "lexical_resource",
    "grammatical_range_accuracy",
    "pronunciation",
)


def _score(criterion: str, band: float, confidence: float = 0.8) -> CriterionScore:
    return CriterionScore(
        criterion=criterion,
        band=band,
        justification=f"Sample justification referencing MLR=4.2 for {criterion}.",
        evidence_features=["MLR=4.2"],
        confidence=confidence,
    )


def _judge_output(bands: dict[str, float]) -> JudgeOutput:
    return JudgeOutput(
        session_id=SESSION_ID,
        criterion_scores=[_score(c, bands[c]) for c in bands],
        overall_band=sum(bands.values()) / len(bands),
        flags=[],
    )


def test_agreeing_passes_are_not_flagged_and_average_the_bands():
    pass_1 = _judge_output({c: 6.0 for c in CRITERIA})
    pass_2 = _judge_output({c: 6.0 for c in CRITERIA})

    result = self_consistency_reconciliation(pass_1, pass_2, threshold=1.0)

    assert result["flag_for_human_review"] is False
    assert result["flagged_criteria"] == []
    assert result["overall_band"] == 6.0
    for entry in result["final_scores"]:
        assert entry["band"] == 6.0
        assert entry["disagreement"] == 0.0


def test_disagreement_within_threshold_is_not_flagged():
    pass_1 = _judge_output({c: 6.0 for c in CRITERIA})
    pass_2 = _judge_output({c: 6.5 for c in CRITERIA})

    result = self_consistency_reconciliation(pass_1, pass_2, threshold=1.0)

    assert result["flag_for_human_review"] is False
    assert result["flagged_criteria"] == []


def test_single_criterion_disagreement_over_threshold_flags_only_that_criterion():
    bands_1 = {c: 6.0 for c in CRITERIA}
    bands_2 = dict(bands_1)
    bands_2["pronunciation"] = 8.0  # 2.0-band gap, exceeds the 1.0 threshold

    result = self_consistency_reconciliation(
        _judge_output(bands_1), _judge_output(bands_2), threshold=1.0
    )

    assert result["flag_for_human_review"] is True
    assert result["flagged_criteria"] == ["pronunciation"]
    assert result["band_diffs"]["pronunciation"] == 2.0
    for criterion in ("fluency_coherence", "lexical_resource", "grammatical_range_accuracy"):
        assert result["band_diffs"][criterion] == 0.0

    pron = next(s for s in result["final_scores"] if s["criterion"] == "pronunciation")
    assert pron["pass_1_band"] == 6.0
    assert pron["pass_2_band"] == 8.0
    assert pron["band"] == 7.0


def test_criterion_missing_from_one_pass_flags_for_human_review():
    bands_1 = {c: 6.0 for c in CRITERIA if c != "pronunciation"}
    bands_2 = {c: 6.0 for c in CRITERIA}

    result = self_consistency_reconciliation(
        _judge_output(bands_1), _judge_output(bands_2), threshold=1.0
    )

    assert result["flag_for_human_review"] is True
    assert result["flagged_criteria"] == ["pronunciation"]
    # A criterion missing from either pass contributes no final_scores
    # entry — there's no band to trust, not even an averaged one.
    assert {s["criterion"] for s in result["final_scores"]} == {
        "fluency_coherence",
        "lexical_resource",
        "grammatical_range_accuracy",
    }
