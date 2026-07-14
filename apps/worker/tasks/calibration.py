"""Calibration & shadow-scoring CLI (Spec 04 §2 Phase 9): batch-runs the
LLM Rubric Judge + self-consistency reconciliation over a benchmark corpus
of sessions with certified human band scores, and reports rater-vs-judge
agreement (MAE/RMSE, exact/adjacent agreement, Pearson/Spearman, QWK --
calibration_metrics.py).

Default mode is a dry run against the bundled synthetic demo corpus
(tests/fixtures/calibration_benchmark/benchmark_corpus_v1.json) using a
deterministic `CorpusScriptedScoringLLM` -- no OPENAI_API_KEY, no
network, no licensed rubric asset required. This is what "runs
successfully on local dev" means for this script. Pass `--live` to score
the corpus with the real `OpenAIScoringLLM` instead (requires
OPENAI_API_KEY and the real licensed rubric asset, Spec 03 §5.1).

Not a Celery task -- this is an offline, operator-invoked calibration
tool, not part of the live per-session grading DAG
(pipelines/grading_pipeline.py); it is not registered in celery_app.py's
`include`/`task_routes`.

Run it as:  cd apps/worker && python -m tasks.calibration [options]
(same cwd-relative import convention every other cross-module import in
this app already relies on -- e.g. this module's own `from
calibration_corpus import ...` below, mirroring tasks/scoring.py's `from
band_score_reports import ...`.)
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from calibration_corpus import CorpusScriptedScoringLLM, DEFAULT_CORPUS_PATH, load_benchmark_corpus
from calibration_report import CalibrationConfig, CalibrationReport, run_calibration
from config import settings
from providers.scoring_llm import OpenAIScoringLLM, ScoringLLM
from rubric_assets import load_rubric_reference

_DRY_RUN_RUBRIC_PLACEHOLDER = (
    "[calibration dry run -- rubric reference not loaded; the scripted "
    "ScoringLLM ignores it entirely and this text is never sent to a real model]"
)


def _build_scoring_llm(args: argparse.Namespace, corpus: list) -> ScoringLLM:
    if not args.live:
        return CorpusScriptedScoringLLM(corpus)
    return OpenAIScoringLLM(model=args.model, system_prompt_suffix=args.prompt_directive)


def _resolve_rubric_reference(args: argparse.Namespace) -> str:
    if not args.live:
        return _DRY_RUN_RUBRIC_PLACEHOLDER
    return load_rubric_reference(settings.rubric_assets_dir)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--corpus", type=Path, default=DEFAULT_CORPUS_PATH, help="Path to a benchmark corpus JSON file."
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Score with the real OpenAIScoringLLM instead of the bundled dry-run scripted passes.",
    )
    parser.add_argument(
        "--model", default=None, help="Overrides settings.scoring_llm_model (only meaningful with --live)."
    )
    parser.add_argument(
        "--prompt-directive",
        default=None,
        help="Extra weighting/emphasis text appended to the judge system prompt "
        "(only meaningful with --live).",
    )
    parser.add_argument(
        "--reconciliation-threshold",
        type=float,
        default=settings.self_consistency_band_disagreement_threshold,
    )
    parser.add_argument(
        "--transcription-confidence-floor",
        type=float,
        default=settings.transcription_confidence_floor,
    )
    parser.add_argument(
        "--pronunciation-confidence-floor",
        type=float,
        default=settings.pronunciation_confidence_floor,
    )
    parser.add_argument("--json-out", type=Path, default=None, help="Write the full report as JSON here.")
    return parser.parse_args(argv)


def run(argv: list[str] | None = None) -> CalibrationReport:
    """Callable form used by tests and by `main()` -- returns the report
    instead of just printing it, so integration tests can assert on it
    directly rather than parsing stdout."""
    args = _parse_args(argv)

    if args.live and not settings.openai_api_key:
        raise SystemExit("error: --live requires OPENAI_API_KEY to be configured")

    corpus = load_benchmark_corpus(args.corpus)
    scoring_llm = _build_scoring_llm(args, corpus)
    rubric_reference = _resolve_rubric_reference(args)

    config = CalibrationConfig(
        reconciliation_threshold=args.reconciliation_threshold,
        judge_prompt_directive=args.prompt_directive,
        transcription_confidence_floor=args.transcription_confidence_floor,
        pronunciation_confidence_floor=args.pronunciation_confidence_floor,
    )

    report = run_calibration(
        corpus, scoring_llm=scoring_llm, config=config, rubric_reference=rubric_reference
    )

    print(report.summary_text())
    if args.json_out:
        args.json_out.write_text(json.dumps(report.to_dict(), indent=2, default=str))
        print(f"\nFull report written to {args.json_out}")

    return report


def main(argv: list[str] | None = None) -> None:
    run(argv)


if __name__ == "__main__":
    main()
