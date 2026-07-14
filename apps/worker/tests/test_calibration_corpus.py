"""calibration_corpus.py unit tests (Spec 04 §2 Phase 9) — no real
Postgres/broker needed. Covers the bundled fixture corpus loading
correctly, duplicate-case_id rejection, human_scores/simulated_pass
validation, and CorpusScriptedScoringLLM's per-session two-pass sequencing.
"""
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from pydantic import ValidationError  # noqa: E402

import calibration_corpus as cc  # noqa: E402
from rubric_assets import CRITERION_ORDER  # noqa: E402

_MINIMAL_CASE_KWARGS = {
    "profile_label": "test_profile",
    "candidate_display_name": "Test Candidate",
    "phases": [],
    "session_aggregate": {},
    "feature_status": {c: "ok" for c in CRITERION_ORDER},
    "human_scores": {c: 6.0 for c in CRITERION_ORDER},
    "simulated_pass_1": {c: 6.0 for c in CRITERION_ORDER},
    "simulated_pass_2": {c: 6.0 for c in CRITERION_ORDER},
}


def _case(case_id: str, **overrides) -> cc.BenchmarkCase:
    kwargs = {**_MINIMAL_CASE_KWARGS, "case_id": case_id, **overrides}
    return cc.BenchmarkCase(**kwargs)


def test_default_corpus_loads_with_five_to_ten_cases():
    corpus = cc.load_benchmark_corpus(cc.DEFAULT_CORPUS_PATH)
    assert 5 <= len(corpus) <= 10
    assert len({case.case_id for case in corpus}) == len(corpus)


def test_benchmark_case_session_id_is_deterministic_per_case_id():
    case_a = _case("bc_x")
    case_b = _case("bc_x")
    case_c = _case("bc_y")
    assert case_a.session_id == case_b.session_id
    assert case_a.session_id != case_c.session_id
    assert isinstance(case_a.session_id, uuid.UUID)


def test_human_scores_must_cover_all_four_criteria():
    incomplete = {c: 6.0 for c in list(CRITERION_ORDER)[:3]}
    with pytest.raises(ValidationError):
        _case("bc_incomplete", human_scores=incomplete)


def test_simulated_passes_must_cover_all_four_criteria():
    incomplete = {c: 6.0 for c in list(CRITERION_ORDER)[:2]}
    with pytest.raises(ValidationError):
        _case("bc_incomplete", simulated_pass_1=incomplete)


def test_load_benchmark_corpus_rejects_duplicate_case_ids(tmp_path):
    payload = {
        "cases": [
            {**_MINIMAL_CASE_KWARGS, "case_id": "dup"},
            {**_MINIMAL_CASE_KWARGS, "case_id": "dup"},
        ]
    }
    path = tmp_path / "dup_corpus.json"
    path.write_text(json.dumps(payload))

    with pytest.raises(ValueError, match="duplicate"):
        cc.load_benchmark_corpus(path)


def test_corpus_scripted_scoring_llm_returns_pass_1_then_pass_2_per_session():
    case_a = _case("a", simulated_pass_1={c: 5.0 for c in CRITERION_ORDER},
                    simulated_pass_2={c: 6.0 for c in CRITERION_ORDER})
    case_b = _case("b", simulated_pass_1={c: 7.0 for c in CRITERION_ORDER},
                    simulated_pass_2={c: 8.0 for c in CRITERION_ORDER})
    llm = cc.CorpusScriptedScoringLLM([case_a, case_b])

    judge_input_a = case_a.to_judge_input(rubric_reference="placeholder")
    judge_input_b = case_b.to_judge_input(rubric_reference="placeholder")

    # Interleaved calls across two sessions -- each session's own call
    # counter must advance independently.
    out_a1 = llm.score(judge_input_a)
    out_b1 = llm.score(judge_input_b)
    out_a2 = llm.score(judge_input_a)
    out_b2 = llm.score(judge_input_b)

    assert {s.band for s in out_a1.criterion_scores} == {5.0}
    assert {s.band for s in out_b1.criterion_scores} == {7.0}
    assert {s.band for s in out_a2.criterion_scores} == {6.0}
    assert {s.band for s in out_b2.criterion_scores} == {8.0}


def test_corpus_scripted_scoring_llm_source_name():
    assert cc.CorpusScriptedScoringLLM([]).source_name == "corpus_scripted"
