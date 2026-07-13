"""self_consistency_reconciliation (Spec 03 §2.2, §5.6): compares the two
independent judge passes per criterion. A >1.0 band disagreement on any
single criterion routes the session to human review rather than being
auto-resolved by averaging — "a >1-band disagreement means the evidence
was ambiguous enough that a certified human rater should adjudicate, not
that the platform should quietly split the difference" (Spec 03 §5.6).
Pure function — no I/O — so it's directly unit-testable without a live
judge or a database.
"""
from providers.scoring_llm import JudgeOutput
from rubric_assets import CRITERION_ORDER


def _round_to_half(value: float) -> float:
    return round(value * 2) / 2


def self_consistency_reconciliation(
    pass_1: JudgeOutput, pass_2: JudgeOutput, *, threshold: float
) -> dict:
    scores_1 = {c.criterion: c for c in pass_1.criterion_scores}
    scores_2 = {c.criterion: c for c in pass_2.criterion_scores}

    final_scores: list[dict] = []
    flagged_criteria: list[str] = []
    band_diffs: dict[str, float] = {}

    for criterion in CRITERION_ORDER:
        c1 = scores_1.get(criterion)
        c2 = scores_2.get(criterion)
        if c1 is None or c2 is None:
            # A pass omitting a criterion entirely is itself evidence the
            # judge couldn't confidently score it — treated as an
            # unresolvable disagreement, never silently dropped.
            flagged_criteria.append(criterion)
            continue

        diff = round(abs(c1.band - c2.band), 2)
        band_diffs[criterion] = diff
        if diff > threshold:
            flagged_criteria.append(criterion)

        final_scores.append(
            {
                "criterion": criterion,
                # The average is always computed and stored as a reference
                # value — even when flagged — per Spec 03 §5.6's "full
                # audit trail is always persisted, regardless of outcome".
                # `flag_for_human_review` is the actionable signal that
                # this value is provisional, not a silently "resolved" one.
                "band": _round_to_half((c1.band + c2.band) / 2),
                "justification": c1.justification,
                "evidence_features": c1.evidence_features,
                "confidence": round(min(c1.confidence, c2.confidence), 3),
                "pass_1_band": c1.band,
                "pass_2_band": c2.band,
                "disagreement": diff,
            }
        )

    overall_band = (
        _round_to_half(sum(s["band"] for s in final_scores) / len(final_scores))
        if final_scores
        else 0.0
    )

    return {
        "final_scores": final_scores,
        "overall_band": overall_band,
        "flag_for_human_review": bool(flagged_criteria),
        "flagged_criteria": flagged_criteria,
        "band_diffs": band_diffs,
    }
