"""calibration_report.py unit tests (Spec 04 §2 Phase 9) — run_calibration()
against a tiny in-memory corpus, no real Postgres/broker/vendor calls
(CorpusScriptedScoringLLM stands in for the judge, same as
test_calibration_corpus.py). Proves the reconciliation-threshold tuning
knob actually changes flagged_for_human_review_count (not just that the
plumbing runs), and that CalibrationReport survives a JSON round-trip.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import calibration_corpus as cc  # noqa: E402
import calibration_report as cr  # noqa: E402
from rubric_assets import CRITERION_ORDER  # noqa: E402

_RUBRIC_PLACEHOLDER = "placeholder rubric text -- unused by CorpusScriptedScoringLLM"


def _agreeing_case(case_id: str, bands: dict[str, float]) -> cc.BenchmarkCase:
    return cc.BenchmarkCase(
        case_id=case_id,
        profile_label="agreeing",
        candidate_display_name="Test Candidate",
        phases=[],
        session_aggregate={},
        feature_status={c: "ok" for c in CRITERION_ORDER},
        human_scores=bands,
        simulated_pass_1=bands,
        simulated_pass_2=bands,
    )


def _disagreeing_case(case_id: str) -> cc.BenchmarkCase:
    pass_1 = {c: 6.0 for c in CRITERION_ORDER}
    pass_2 = dict(pass_1)
    pass_2["pronunciation"] = 8.5  # 2.5-band gap -- exceeds every sane threshold below 2.5
    return cc.BenchmarkCase(
        case_id=case_id,
        profile_label="disagreeing",
        candidate_display_name="Test Candidate",
        phases=[],
        session_aggregate={},
        feature_status={c: "ok" for c in CRITERION_ORDER},
        human_scores={c: 6.0 for c in CRITERION_ORDER},
        simulated_pass_1=pass_1,
        simulated_pass_2=pass_2,
    )


def test_run_calibration_on_perfectly_agreeing_corpus():
    corpus = [
        _agreeing_case("c1", {c: 6.0 for c in CRITERION_ORDER}),
        _agreeing_case("c2", {c: 7.0 for c in CRITERION_ORDER}),
    ]
    llm = cc.CorpusScriptedScoringLLM(corpus)

    report = cr.run_calibration(corpus, scoring_llm=llm, rubric_reference=_RUBRIC_PLACEHOLDER)

    assert report.corpus_size == 2
    assert report.flagged_for_human_review_count == 0
    for m in report.criterion_metrics:
        assert m.n == 2
        assert m.mae == 0.0
        assert m.exact_agreement_rate == 1.0


def test_reconciliation_threshold_tuning_changes_flagged_count():
    corpus = [_disagreeing_case("d1"), _disagreeing_case("d2")]
    llm = cc.CorpusScriptedScoringLLM(corpus)

    default_report = cr.run_calibration(corpus, scoring_llm=llm, rubric_reference=_RUBRIC_PLACEHOLDER)
    assert default_report.flagged_for_human_review_count == 2

    # A high enough threshold makes the exact same 2.5-band disagreement
    # no longer flag-worthy -- this is the concrete proof the tuning knob
    # changes outcomes, not just that it's accepted as a parameter.
    llm2 = cc.CorpusScriptedScoringLLM(corpus)
    tuned_config = cr.CalibrationConfig(reconciliation_threshold=5.0)
    tuned_report = cr.run_calibration(
        corpus, scoring_llm=llm2, config=tuned_config, rubric_reference=_RUBRIC_PLACEHOLDER
    )
    assert tuned_report.flagged_for_human_review_count == 0


def test_fallback_gating_dry_run_reports_cases_below_floor():
    low_asr_case = cc.BenchmarkCase(
        case_id="low_asr",
        profile_label="low_asr",
        candidate_display_name="Test Candidate",
        phases=[],
        session_aggregate={},
        feature_status={c: "ok" for c in CRITERION_ORDER},
        human_scores={c: 6.0 for c in CRITERION_ORDER},
        simulated_pass_1={c: 6.0 for c in CRITERION_ORDER},
        simulated_pass_2={c: 6.0 for c in CRITERION_ORDER},
        asr_word_confidence=0.5,
        pronunciation_confidence=0.9,
    )
    fine_case = cc.BenchmarkCase(
        case_id="fine",
        profile_label="fine",
        candidate_display_name="Test Candidate",
        phases=[],
        session_aggregate={},
        feature_status={c: "ok" for c in CRITERION_ORDER},
        human_scores={c: 6.0 for c in CRITERION_ORDER},
        simulated_pass_1={c: 6.0 for c in CRITERION_ORDER},
        simulated_pass_2={c: 6.0 for c in CRITERION_ORDER},
        asr_word_confidence=0.95,
        pronunciation_confidence=0.95,
    )

    result = cr.fallback_gating_dry_run(
        [low_asr_case, fine_case],
        transcription_confidence_floor=0.7,
        pronunciation_confidence_floor=0.7,
    )

    assert result["asr_fallback_triggered_case_ids"] == ["low_asr"]
    assert result["pronunciation_fallback_triggered_case_ids"] == []


def test_calibration_report_survives_json_round_trip():
    corpus = [_agreeing_case("c1", {c: 6.0 for c in CRITERION_ORDER})]
    llm = cc.CorpusScriptedScoringLLM(corpus)
    report = cr.run_calibration(corpus, scoring_llm=llm, rubric_reference=_RUBRIC_PLACEHOLDER)

    round_tripped = json.loads(json.dumps(report.to_dict()))
    assert round_tripped["corpus_size"] == 1
    assert round_tripped["scoring_llm_source"] == "corpus_scripted"
    assert len(round_tripped["criterion_metrics"]) == 4


def test_summary_text_is_nonempty_and_mentions_corpus_size():
    corpus = [_agreeing_case("c1", {c: 6.0 for c in CRITERION_ORDER})]
    llm = cc.CorpusScriptedScoringLLM(corpus)
    report = cr.run_calibration(corpus, scoring_llm=llm, rubric_reference=_RUBRIC_PLACEHOLDER)

    text = report.summary_text()
    assert "corpus_size=1" in text
