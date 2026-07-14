"""Pilot verification test (Spec 04 §2 Phase 9 exit criterion): loads the
bundled benchmark corpus of 5-10 simulated sessions with varying
performance profiles (tests/fixtures/calibration_benchmark/
benchmark_corpus_v1.json), runs them through the calibration evaluation in
dry-run mode (CorpusScriptedScoringLLM — no real Postgres/vendor calls
needed, unlike this repo's other tests/integration/ suites), and verifies
the scoring math parses, calculates every requested metric, and produces a
correct, JSON-serializable statistical summary.

Lives under tests/integration/ per the Phase 9 request's explicit
instruction, even though — unlike its neighbors — it needs no real
Postgres/MinIO: it's still an end-to-end check across
calibration_corpus.py + calibration_report.py + reconciliation.py
together, not a single pure-function unit test.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "apps" / "worker"))

import calibration_corpus as cc  # noqa: E402
import calibration_report as cr  # noqa: E402
from rubric_assets import CRITERION_ORDER  # noqa: E402

# Known by construction (see the fixture's own case-by-case comments):
# bc_006 scripts a >1.0-band pronunciation gap between its two passes at
# the default 1.0 reconciliation threshold, and is the only such case in
# the corpus. bc_007's asr_word_confidence and bc_006's
# pronunciation_confidence are the only two values below the default 0.7
# floors.
EXPECTED_FLAGGED_CASE_IDS = {"bc_006"}
EXPECTED_ASR_FALLBACK_CASE_IDS = {"bc_007"}
EXPECTED_PRONUNCIATION_FALLBACK_CASE_IDS = {"bc_006"}


def _load_corpus_and_run(config: cr.CalibrationConfig | None = None) -> tuple[list, cr.CalibrationReport]:
    corpus = cc.load_benchmark_corpus(cc.DEFAULT_CORPUS_PATH)
    llm = cc.CorpusScriptedScoringLLM(corpus)
    report = cr.run_calibration(
        corpus,
        scoring_llm=llm,
        config=config,
        rubric_reference="pilot-test placeholder rubric reference",
    )
    return corpus, report


def test_bundled_corpus_has_five_to_ten_varied_profile_cases():
    corpus = cc.load_benchmark_corpus(cc.DEFAULT_CORPUS_PATH)
    assert 5 <= len(corpus) <= 10
    # "Varying performance profiles" -- every case must actually be
    # distinct, not N copies of one profile.
    assert len({case.profile_label for case in corpus}) == len(corpus)


def test_calibration_evaluation_produces_a_well_formed_statistical_summary():
    corpus, report = _load_corpus_and_run()

    assert report.corpus_size == len(corpus)
    assert report.scoring_llm_source == "corpus_scripted"
    assert {m.criterion for m in report.criterion_metrics} == set(CRITERION_ORDER)

    for metrics in (*report.criterion_metrics, report.overall_band_metrics):
        assert metrics.n == len(corpus)
        assert metrics.mae >= 0.0
        assert metrics.rmse >= 0.0
        assert 0.0 <= metrics.exact_agreement_rate <= 1.0
        assert 0.0 <= metrics.adjacent_agreement_rate_0_5 <= 1.0
        assert 0.0 <= metrics.adjacent_agreement_rate_1_0 <= 1.0
        # Exact agreement can only be rarer or equal to the wider bands.
        assert metrics.exact_agreement_rate <= metrics.adjacent_agreement_rate_0_5
        assert metrics.adjacent_agreement_rate_0_5 <= metrics.adjacent_agreement_rate_1_0
        if metrics.pearson_r is not None:
            assert -1.0 <= metrics.pearson_r <= 1.0
        if metrics.spearman_rho is not None:
            assert -1.0 <= metrics.spearman_rho <= 1.0
        if metrics.quadratic_weighted_kappa is not None:
            assert -1.0 <= metrics.quadratic_weighted_kappa <= 1.0


def test_deliberately_scripted_disagreement_case_is_flagged_for_human_review():
    _, report = _load_corpus_and_run()

    flagged_case_ids = {case["case_id"] for case in report.per_case if case["flag_for_human_review"]}
    assert flagged_case_ids == EXPECTED_FLAGGED_CASE_IDS
    assert report.flagged_for_human_review_count == len(EXPECTED_FLAGGED_CASE_IDS)
    assert report.flagged_for_human_review_rate == len(EXPECTED_FLAGGED_CASE_IDS) / report.corpus_size


def test_fallback_gating_dry_run_matches_the_corpus_recorded_confidence_values():
    _, report = _load_corpus_and_run()

    assert set(report.fallback_gating["asr_fallback_triggered_case_ids"]) == (
        EXPECTED_ASR_FALLBACK_CASE_IDS
    )
    assert set(report.fallback_gating["pronunciation_fallback_triggered_case_ids"]) == (
        EXPECTED_PRONUNCIATION_FALLBACK_CASE_IDS
    )


def test_raising_the_reconciliation_threshold_reduces_flags_on_the_real_corpus():
    _, default_report = _load_corpus_and_run()
    assert default_report.flagged_for_human_review_count >= 1

    _, tuned_report = _load_corpus_and_run(cr.CalibrationConfig(reconciliation_threshold=5.0))
    assert tuned_report.flagged_for_human_review_count == 0


def test_report_survives_json_serialization_end_to_end():
    import json

    _, report = _load_corpus_and_run()
    serialized = json.dumps(report.to_dict())
    round_tripped = json.loads(serialized)

    assert round_tripped["corpus_size"] == report.corpus_size
    assert len(round_tripped["per_case"]) == report.corpus_size
    assert round_tripped["config"]["reconciliation_threshold"] == report.config.reconciliation_threshold
