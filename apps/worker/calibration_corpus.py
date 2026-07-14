"""Benchmark corpus loading for the Spec 04 §2 Phase 9 calibration pipeline.

A `BenchmarkCase` is a synthetic/simulated exam session — pre-computed
feature vectors and a transcript, exactly the shape `JudgeInput` already
expects (Spec 03 §5.3) — paired with a certified human rater's gold band
scores and two scripted judge passes for dry-run shadow-scoring.

Unlike the licensed rubric text (Spec 01 §7, rubric_assets.py), this
corpus carries no licensing or PII concern: it's hand-authored synthetic
data, not real candidate sessions, so it's committed to source control as
a test fixture (same posture as tests/fixtures/reference_audio/'s
golden-file corpora), not injected via the secret store.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, field_validator

from providers.scoring_llm import CriterionScore, JudgeInput, JudgeOutput, PhaseEvidence
from rubric_assets import CRITERION_ORDER

# apps/worker/calibration_corpus.py -> repo root is 2 parents up (same
# pattern as config.py's _REPO_ROOT).
_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CORPUS_PATH = (
    _REPO_ROOT / "tests" / "fixtures" / "calibration_benchmark" / "benchmark_corpus_v1.json"
)

# A fixed namespace so a given case_id always maps to the same UUID across
# runs/processes — BenchmarkCase.session_id must be deterministic for
# CorpusScriptedScoringLLM to correlate two .score() calls for the same
# case (pass 1, then pass 2).
_CALIBRATION_UUID_NAMESPACE = uuid.UUID("6f6f1e2a-6c1b-4a7b-9e2a-8f2e7c9a0b11")


def _require_all_criteria(value: dict[str, float], field_name: str) -> dict[str, float]:
    missing = set(CRITERION_ORDER) - set(value)
    if missing:
        raise ValueError(f"{field_name} is missing criteria: {sorted(missing)}")
    return value


class BenchmarkCase(BaseModel):
    case_id: str
    profile_label: str
    candidate_display_name: str
    phases: list[PhaseEvidence]
    session_aggregate: dict[str, dict]
    feature_status: dict[str, Literal["ok", "missing", "low_confidence"]]
    human_scores: dict[str, float]
    simulated_pass_1: dict[str, float]
    simulated_pass_2: dict[str, float]
    # Informational only (Spec 04 §2 Phase 9's fallback-gating dry run,
    # calibration_report.py) — what the (simulated) upstream ASR/
    # pronunciation pipeline reported for this case, not re-derived here.
    asr_word_confidence: float | None = None
    pronunciation_confidence: float | None = None

    @field_validator("human_scores")
    @classmethod
    def _validate_human_scores(cls, value: dict[str, float]) -> dict[str, float]:
        return _require_all_criteria(value, "human_scores")

    @field_validator("simulated_pass_1")
    @classmethod
    def _validate_simulated_pass_1(cls, value: dict[str, float]) -> dict[str, float]:
        return _require_all_criteria(value, "simulated_pass_1")

    @field_validator("simulated_pass_2")
    @classmethod
    def _validate_simulated_pass_2(cls, value: dict[str, float]) -> dict[str, float]:
        return _require_all_criteria(value, "simulated_pass_2")

    @property
    def session_id(self) -> uuid.UUID:
        return uuid.uuid5(_CALIBRATION_UUID_NAMESPACE, self.case_id)

    def to_judge_input(self, *, rubric_reference: str) -> JudgeInput:
        return JudgeInput(
            session_id=self.session_id,
            candidate_display_name=self.candidate_display_name,
            phases=self.phases,
            session_aggregate=self.session_aggregate,
            rubric_reference=rubric_reference,
            feature_status=self.feature_status,
        )


def load_benchmark_corpus(path: Path) -> list[BenchmarkCase]:
    data = json.loads(path.read_text())
    cases = [BenchmarkCase(**case) for case in data["cases"]]

    case_ids = [case.case_id for case in cases]
    if len(case_ids) != len(set(case_ids)):
        duplicates = sorted({c for c in case_ids if case_ids.count(c) > 1})
        raise ValueError(f"duplicate case_id(s) in benchmark corpus at {path}: {duplicates}")

    return cases


class CorpusScriptedScoringLLM:
    """A deterministic, multi-session-aware `ScoringLLM` (Spec 03 §5.2's
    swappable interface) for dry-run shadow-scoring — the calibration
    sibling of Phase 7's single-session `FixtureScoringLLM`
    (tests/integration/test_synthesize_band_scores.py). Keyed by
    `session_id` rather than a flat call counter, since `run_calibration`
    scores every case in the corpus through one shared instance: the first
    `.score()` call for a given session returns that case's
    `simulated_pass_1`, the second returns `simulated_pass_2` — the exact
    two-pass shape `run_calibration` (and the live pipeline's
    `synthesize_band_scores`) always drives a `ScoringLLM` through.
    """

    source_name = "corpus_scripted"

    def __init__(self, corpus: list[BenchmarkCase]) -> None:
        self._by_session: dict[uuid.UUID, BenchmarkCase] = {c.session_id: c for c in corpus}
        self._call_counts: dict[uuid.UUID, int] = {}

    def score(self, judge_input: JudgeInput) -> JudgeOutput:
        case = self._by_session[judge_input.session_id]
        call_index = self._call_counts.get(judge_input.session_id, 0)
        self._call_counts[judge_input.session_id] = call_index + 1
        bands = case.simulated_pass_1 if call_index == 0 else case.simulated_pass_2

        return JudgeOutput(
            session_id=judge_input.session_id,
            criterion_scores=[
                CriterionScore(
                    criterion=criterion,
                    band=bands[criterion],
                    justification=(
                        f"Scripted calibration pass for corpus case "
                        f"{case.case_id!r} ({case.profile_label})."
                    ),
                    evidence_features=[],
                    confidence=0.9,
                )
                for criterion in CRITERION_ORDER
            ],
            overall_band=round(sum(bands.values()) / len(bands) * 2) / 2,
            flags=[],
        )
