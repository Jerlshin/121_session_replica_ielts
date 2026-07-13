"""Loads the licensed IELTS band-descriptor reference text (Spec 03 §5.1)
server-side, at judge-call time — never hardcoded into source control.

`packages/grading-rubric-assets/` is deliberately empty in this repository
(Spec 04 §1 tree comment: "actual asset injected via secret store at
deploy time"). The real `band_descriptors_v1.json` must be placed at
`settings.rubric_assets_dir` by the deploy/ops process, not committed here
— this module only knows the expected shape and how to render it into the
judge prompt's reference text. A missing file is a real operational gap
(the asset hasn't been deployed yet), not a placeholder to paper over.
"""
import json
from pathlib import Path

# Ordered so the rendered reference text lists criteria in the same order
# CriterionScore.criterion enumerates them (Spec 03 §5.4).
CRITERION_ORDER = (
    "fluency_coherence",
    "lexical_resource",
    "grammatical_range_accuracy",
    "pronunciation",
)


class RubricAssetError(RuntimeError):
    """Raised when the licensed rubric asset is missing or malformed."""


def load_rubric_reference(assets_dir: Path, version: str = "v1") -> str:
    path = assets_dir / f"band_descriptors_{version}.json"
    if not path.exists():
        raise RubricAssetError(
            f"licensed rubric asset not found at {path} — see Spec 01 §7 / Spec 03 §5.1: "
            "this must be injected via the secret store at deploy time, it is never "
            "committed to source control"
        )

    try:
        data = json.loads(path.read_text())
        criteria = data["criteria"]
    except (json.JSONDecodeError, KeyError) as exc:
        raise RubricAssetError(f"malformed rubric asset at {path}: {exc}") from exc

    sections = []
    for criterion in CRITERION_ORDER:
        bands = criteria.get(criterion)
        if not bands:
            continue
        lines = [criterion.replace("_", " ").title()]
        for band in sorted(bands, key=lambda b: -float(b)):
            lines.append(f"Band {band}: {bands[band]}")
        sections.append("\n".join(lines))

    return "\n\n".join(sections)
