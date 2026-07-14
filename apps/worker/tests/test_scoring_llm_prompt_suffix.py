"""build_judge_system_prompt() unit tests (Spec 04 §2 Phase 9's judge-
prompt tuning knob) — no network call, pins both the default (no-suffix)
output as byte-identical to what OpenAIScoringLLM always rendered, and the
with-suffix calibration-directive path.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from providers.scoring_llm import JUDGE_SYSTEM_PROMPT, build_judge_system_prompt  # noqa: E402

_RUBRIC_REFERENCE = "Band 9: expert user.\nBand 8: very good user."


def test_no_suffix_produces_byte_identical_output_to_the_original_inline_format_call():
    expected = JUDGE_SYSTEM_PROMPT.format(rubric_reference=_RUBRIC_REFERENCE)
    assert build_judge_system_prompt(_RUBRIC_REFERENCE) == expected
    assert build_judge_system_prompt(_RUBRIC_REFERENCE, directive_suffix=None) == expected


def test_empty_string_suffix_is_treated_as_no_suffix():
    expected = JUDGE_SYSTEM_PROMPT.format(rubric_reference=_RUBRIC_REFERENCE)
    assert build_judge_system_prompt(_RUBRIC_REFERENCE, directive_suffix="") == expected


def test_directive_suffix_is_appended_in_a_delimited_block():
    directive = "Weight grammatical accuracy more heavily than lexical sophistication."
    prompt = build_judge_system_prompt(_RUBRIC_REFERENCE, directive_suffix=directive)

    assert prompt.startswith(JUDGE_SYSTEM_PROMPT.format(rubric_reference=_RUBRIC_REFERENCE))
    assert "<<CALIBRATION_DIRECTIVE>>" in prompt
    assert directive in prompt
    assert "<<END_CALIBRATION_DIRECTIVE>>" in prompt
