"""The LLM Rubric Judge (Spec 03 §5): a model-agnostic `ScoringLLM`
interface, default production implementation on OpenAI in structured
output mode (Spec 03 §5.2). The live conversational model (Gemini) and
this judge solve different problems — real-time duplex audio vs. offline,
schema-constrained structured output over a large evidence payload — so
this is a swappable adapter, never a hard dependency on one vendor.

Real vendor code, gated behind `OPENAI_API_KEY`; never exercised in CI
(same posture as Deepgram/Azure/LanguageTool in earlier phases) — a
deterministic `FixtureScoringLLM` (test-only) proves the reconciliation and
report-synthesis pipeline instead.
"""
from __future__ import annotations

import uuid
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from config import settings

# --- Spec 03 §5.3 — Judge input schema -------------------------------------


class PhaseEvidence(BaseModel):
    phase: Literal["part1", "part2", "part3"]
    transcript_text: str  # candidate turns only, this phase
    fluency_features: dict
    lexical_features: dict
    grammar_features: dict
    pronunciation_features: dict


class JudgeInput(BaseModel):
    session_id: uuid.UUID
    candidate_display_name: str
    phases: list[PhaseEvidence]
    session_aggregate: dict[str, dict]  # FC/LR/GRA/P rolled up across the whole exam
    rubric_reference: str  # loaded server-side from the licensed asset, §5.1
    feature_status: dict[str, Literal["ok", "missing", "low_confidence"]]


# --- Spec 03 §5.4 — Judge output schema -------------------------------------


class CriterionScore(BaseModel):
    criterion: Literal[
        "fluency_coherence", "lexical_resource", "grammatical_range_accuracy", "pronunciation"
    ]
    band: float = Field(description="0.0-9.0, 0.5 increments")
    justification: str = Field(
        description="2-4 sentences, must name specific feature(s) used"
    )
    evidence_features: list[str] = Field(
        description='e.g. ["MLR=4.2", "filled_pause_rate=9.1/100w"]'
    )
    confidence: float = Field(description="0.0-1.0")


class JudgeOutput(BaseModel):
    session_id: uuid.UUID
    criterion_scores: list[CriterionScore]  # exactly 4
    overall_band: float  # mean of the 4 criterion bands, rounded to nearest 0.5
    flags: list[str]  # e.g. ["language_mismatch_part3", "low_confidence_pronunciation"]


# --- Spec 03 §5.5 — Prompt template -----------------------------------------

JUDGE_SYSTEM_PROMPT = """\
You are an IELTS Speaking rubric auditor. You will be given, for a single
candidate's exam session: their transcript per part, and a set of
pre-computed linguistic features (fluency, lexical, grammatical,
pronunciation) already extracted by deterministic analysis pipelines.

Your job is to map this evidence onto the official band descriptors provided
below, and output a score. You do not re-derive fluency, vocabulary
sophistication, grammatical accuracy, or pronunciation quality from your own
impression of the transcript — you interpret the computed features against
the descriptors. Every justification you write must explicitly reference at
least one specific feature value you were given.

If feature_status marks any criterion as "missing" or "low_confidence" for a
phase, say so plainly in that criterion's justification and lower your
stated confidence accordingly — do not paper over a gap.

<<OFFICIAL_BAND_DESCRIPTORS>>
{rubric_reference}
<<END_OFFICIAL_BAND_DESCRIPTORS>>

Respond only with JSON matching the provided schema. No prose outside the
JSON object.
"""


class ScoringLLM(Protocol):
    source_name: str

    def score(self, judge_input: JudgeInput) -> JudgeOutput: ...


class ScoringLLMError(RuntimeError):
    """Raised when the judge cannot produce a scored output at all."""


def build_judge_system_prompt(rubric_reference: str, *, directive_suffix: str | None = None) -> str:
    """Renders the judge system prompt, optionally with an extra
    calibration directive appended (Spec 04 §2 Phase 9 — "calibrate the
    Judge prompt... weighting directives"). `directive_suffix=None`
    (the default, used everywhere outside calibration) produces the exact
    same prompt text this always rendered, byte for byte."""
    prompt = JUDGE_SYSTEM_PROMPT.format(rubric_reference=rubric_reference)
    if directive_suffix:
        prompt = (
            f"{prompt}\n\n<<CALIBRATION_DIRECTIVE>>\n{directive_suffix}\n"
            "<<END_CALIBRATION_DIRECTIVE>>"
        )
    return prompt


class OpenAIScoringLLM:
    """Default production `ScoringLLM` (Spec 03 §5.2) — OpenAI in
    structured-output mode via `client.responses.parse()`. Real vendor
    call, gated behind `OPENAI_API_KEY`; never exercised in CI.

    Spec 03 §5.6 asks for the two self-consistency passes to run "at low
    temperature". The default model here (`gpt-5.1`) is a reasoning model,
    and like Claude's 4.7-generation models the Responses API rejects
    `temperature`/`top_p` for reasoning models — any value returns a 400.
    Two independent passes still gives a genuine self-consistency signal,
    since sampling is not deterministic even without an explicit
    low-temperature setting; this is a documented deviation forced by a
    vendor API constraint, not an oversight (same posture as the prior
    Claude implementation).
    """

    source_name = "openai"

    def __init__(self, model: str | None = None, system_prompt_suffix: str | None = None) -> None:
        self._model = model or settings.scoring_llm_model
        # Calibration-tuning hook (Spec 04 §2 Phase 9) — unset in every
        # non-calibration caller, so default behavior is unchanged.
        self._system_prompt_suffix = system_prompt_suffix

    def score(self, judge_input: JudgeInput) -> JudgeOutput:
        if not settings.openai_api_key:
            raise ScoringLLMError("OPENAI_API_KEY is not configured")

        import openai

        client = openai.OpenAI(api_key=settings.openai_api_key)
        system_prompt = build_judge_system_prompt(
            judge_input.rubric_reference, directive_suffix=self._system_prompt_suffix
        )

        try:
            response = client.responses.parse(
                model=self._model,
                input=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": judge_input.model_dump_json()},
                ],
                text_format=JudgeOutput,
            )
        except openai.OpenAIError as exc:
            raise ScoringLLMError(f"OpenAI scoring request failed: {exc}") from exc

        if response.output_parsed is None:
            raise ScoringLLMError("OpenAI did not return a schema-valid JudgeOutput")
        return response.output_parsed
