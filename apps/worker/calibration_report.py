"""Batch shadow-scoring + agreement reporting (Spec 04 §2 Phase 9): runs a
benchmark corpus through the same judge-scoring + self-consistency
reconciliation machinery the live pipeline uses
(tasks/scoring.py::synthesize_band_scores), then measures rater-vs-judge
agreement against each case's certified human scores.

Deliberately reuses `reconciliation.self_consistency_reconciliation`
unmodified rather than reimplementing reconciliation logic here — that's
what makes the reconciliation-threshold tuning knob (`CalibrationConfig.
reconciliation_threshold`) a genuine test of the same code path production
runs, not a parallel approximation of it.
"""
from __future__ import annotations

import dataclasses

import calibration_metrics
from calibration_corpus import BenchmarkCase
from providers.scoring_llm import ScoringLLM
from reconciliation import self_consistency_reconciliation
from rubric_assets import CRITERION_ORDER

# Standalone defaults mirroring config.py's own defaults (Spec 03 §5.6,
# Spec 03 §3/§4.4) -- this module deliberately does not import `settings`,
# so it stays a pure, settings-agnostic reporting layer; the CLI
# (tasks/calibration.py) is the one place that reads real settings and
# passes values in explicitly.
_DEFAULT_RECONCILIATION_THRESHOLD = 1.0
_DEFAULT_TRANSCRIPTION_CONFIDENCE_FLOOR = 0.7
_DEFAULT_PRONUNCIATION_CONFIDENCE_FLOOR = 0.7


def _round_to_half(value: float) -> float:
    return round(value * 2) / 2


@dataclasses.dataclass
class CalibrationConfig:
    """The four tuning knobs Spec 04 §2 Phase 9 asks for: the judge
    prompt's calibration directive, the self-consistency reconciliation
    disagreement threshold (Spec 03 §5.6, currently 1.0 in production),
    and the ASR/pronunciation low-confidence fallback floors (Spec 03
    §3/§4.4)."""

    reconciliation_threshold: float = _DEFAULT_RECONCILIATION_THRESHOLD
    judge_prompt_directive: str | None = None
    transcription_confidence_floor: float = _DEFAULT_TRANSCRIPTION_CONFIDENCE_FLOOR
    pronunciation_confidence_floor: float = _DEFAULT_PRONUNCIATION_CONFIDENCE_FLOOR


@dataclasses.dataclass
class CriterionMetrics:
    criterion: str
    n: int
    mae: float
    rmse: float
    exact_agreement_rate: float
    adjacent_agreement_rate_0_5: float
    adjacent_agreement_rate_1_0: float
    pearson_r: float | None
    spearman_rho: float | None
    quadratic_weighted_kappa: float | None


@dataclasses.dataclass
class CalibrationReport:
    corpus_size: int
    config: CalibrationConfig
    scoring_llm_source: str
    criterion_metrics: list[CriterionMetrics]
    overall_band_metrics: CriterionMetrics
    flagged_for_human_review_count: int
    flagged_for_human_review_rate: float
    fallback_gating: dict
    per_case: list[dict]

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    def summary_text(self) -> str:
        lines = [
            f"Calibration report -- corpus_size={self.corpus_size} "
            f"scoring_llm={self.scoring_llm_source} "
            f"reconciliation_threshold={self.config.reconciliation_threshold}",
            f"flagged_for_human_review: {self.flagged_for_human_review_count}/"
            f"{self.corpus_size} ({self.flagged_for_human_review_rate:.1%})",
            "",
            f"{'criterion':<28}{'n':>4}{'MAE':>7}{'RMSE':>7}{'exact':>8}{'adj0.5':>8}"
            f"{'adj1.0':>8}{'pearson':>9}{'spearman':>10}{'QWK':>8}",
        ]
        for m in (*self.criterion_metrics, self.overall_band_metrics):
            lines.append(
                f"{m.criterion:<28}{m.n:>4}{m.mae:>7.2f}{m.rmse:>7.2f}"
                f"{m.exact_agreement_rate:>8.1%}{m.adjacent_agreement_rate_0_5:>8.1%}"
                f"{m.adjacent_agreement_rate_1_0:>8.1%}"
                f"{_fmt_or_na(m.pearson_r):>9}{_fmt_or_na(m.spearman_rho):>10}"
                f"{_fmt_or_na(m.quadratic_weighted_kappa):>8}"
            )
        lines.append("")
        lines.append(
            f"fallback gating dry run (floors: transcription="
            f"{self.fallback_gating['transcription_confidence_floor']}, "
            f"pronunciation={self.fallback_gating['pronunciation_confidence_floor']}):"
        )
        lines.append(
            f"  ASR fallback would trigger for: "
            f"{self.fallback_gating['asr_fallback_triggered_case_ids'] or '(none)'}"
        )
        lines.append(
            f"  Pronunciation fallback would trigger for: "
            f"{self.fallback_gating['pronunciation_fallback_triggered_case_ids'] or '(none)'}"
        )
        return "\n".join(lines)


def _fmt_or_na(value: float | None) -> str:
    return f"{value:.3f}" if value is not None else "n/a"


def fallback_gating_dry_run(
    corpus: list[BenchmarkCase],
    *,
    transcription_confidence_floor: float,
    pronunciation_confidence_floor: float,
) -> dict:
    """Reports which corpus cases' *recorded* confidence values would
    trigger Phase 5/6's real low-confidence fallback gating (Deepgram ->
    WhisperX, Azure -> GOP) at the given thresholds -- informational only.
    This does not re-run real ASR/pronunciation extraction (the corpus
    stores pre-computed feature vectors, not raw audio); it exists so an
    operator can systematically sweep these thresholds and see the effect
    on how many benchmark cases *would* have been routed through the
    fallback path, without needing real vendor calls.
    """
    asr_triggered = [
        case.case_id
        for case in corpus
        if case.asr_word_confidence is not None
        and case.asr_word_confidence < transcription_confidence_floor
    ]
    pronunciation_triggered = [
        case.case_id
        for case in corpus
        if case.pronunciation_confidence is not None
        and case.pronunciation_confidence < pronunciation_confidence_floor
    ]
    return {
        "transcription_confidence_floor": transcription_confidence_floor,
        "pronunciation_confidence_floor": pronunciation_confidence_floor,
        "asr_fallback_triggered_case_ids": asr_triggered,
        "pronunciation_fallback_triggered_case_ids": pronunciation_triggered,
    }


def _compute_criterion_metrics(
    criterion: str, preds: list[float], golds: list[float]
) -> CriterionMetrics:
    return CriterionMetrics(
        criterion=criterion,
        n=len(preds),
        mae=calibration_metrics.mean_absolute_error(preds, golds),
        rmse=calibration_metrics.root_mean_squared_error(preds, golds),
        exact_agreement_rate=calibration_metrics.agreement_rate(preds, golds, tolerance=0.0),
        adjacent_agreement_rate_0_5=calibration_metrics.agreement_rate(preds, golds, tolerance=0.5),
        adjacent_agreement_rate_1_0=calibration_metrics.agreement_rate(preds, golds, tolerance=1.0),
        pearson_r=calibration_metrics.pearson_correlation(preds, golds),
        spearman_rho=calibration_metrics.spearman_correlation(preds, golds),
        quadratic_weighted_kappa=calibration_metrics.quadratic_weighted_kappa(preds, golds),
    )


def run_calibration(
    corpus: list[BenchmarkCase],
    *,
    scoring_llm: ScoringLLM,
    config: CalibrationConfig | None = None,
    rubric_reference: str,
) -> CalibrationReport:
    config = config or CalibrationConfig()

    per_case_results: list[tuple[BenchmarkCase, dict]] = []
    for case in corpus:
        judge_input = case.to_judge_input(rubric_reference=rubric_reference)
        # Two independent passes (Spec 03 §5.6) -- the same shape
        # synthesize_band_scores drives a ScoringLLM through in production.
        pass_1 = scoring_llm.score(judge_input)
        pass_2 = scoring_llm.score(judge_input)
        reconciliation = self_consistency_reconciliation(
            pass_1, pass_2, threshold=config.reconciliation_threshold
        )
        per_case_results.append((case, reconciliation))

    criterion_metrics = []
    for criterion in CRITERION_ORDER:
        preds: list[float] = []
        golds: list[float] = []
        for case, reconciliation in per_case_results:
            final = next(
                (s for s in reconciliation["final_scores"] if s["criterion"] == criterion), None
            )
            if final is None:
                # Excluded from final_scores entirely (missing from a pass,
                # or -- not applicable here since reconciliation always
                # computes a reference band regardless of the flag) --
                # there's no judge band to compare against the human gold.
                continue
            preds.append(final["band"])
            golds.append(case.human_scores[criterion])
        criterion_metrics.append(_compute_criterion_metrics(criterion, preds, golds))

    overall_preds = [reconciliation["overall_band"] for _, reconciliation in per_case_results]
    overall_golds = [
        _round_to_half(sum(case.human_scores.values()) / len(case.human_scores))
        for case, _ in per_case_results
    ]
    overall_metrics = _compute_criterion_metrics("overall", overall_preds, overall_golds)

    flagged_count = sum(
        1 for _, reconciliation in per_case_results if reconciliation["flag_for_human_review"]
    )

    per_case_summaries = [
        {
            "case_id": case.case_id,
            "profile_label": case.profile_label,
            "flag_for_human_review": reconciliation["flag_for_human_review"],
            "flagged_criteria": reconciliation["flagged_criteria"],
            "predicted": {s["criterion"]: s["band"] for s in reconciliation["final_scores"]},
            "human": case.human_scores,
        }
        for case, reconciliation in per_case_results
    ]

    return CalibrationReport(
        corpus_size=len(corpus),
        config=config,
        scoring_llm_source=scoring_llm.source_name,
        criterion_metrics=criterion_metrics,
        overall_band_metrics=overall_metrics,
        flagged_for_human_review_count=flagged_count,
        flagged_for_human_review_rate=(flagged_count / len(corpus) if corpus else 0.0),
        fallback_gating=fallback_gating_dry_run(
            corpus,
            transcription_confidence_floor=config.transcription_confidence_floor,
            pronunciation_confidence_floor=config.pronunciation_confidence_floor,
        ),
        per_case=per_case_summaries,
    )
