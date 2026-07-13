"""Grammar/lexical-appropriacy checking (Spec 03 §4.2, §4.3): LanguageTool
as the real primary checker, gated behind an interface — same posture as
Deepgram/WhisperX in Phase 5. The spec's "neural grammar-error-detection
tagger as a second opinion" is a documented extension point on this
interface, not implemented — no concrete second vendor is named in the
spec to build against.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class GrammarError:
    category: str  # e.g. "GRAMMAR", "TYPOS", "COLLOCATIONS", "STYLE"
    rule_id: str
    message: str
    offset: int  # character offset into the checked text
    length: int


class GrammarCheckProvider(Protocol):
    source_name: str

    def check(self, text: str) -> list[GrammarError]: ...


class LanguageToolProvider:
    """Real LanguageTool integration (Spec 03 §4.3) — lazy-imported since
    `language_tool_python` wraps a JRE and a ~200MB server jar downloaded
    on first real use. Install the `apps/worker[languagetool]` extra to
    use this in production; never exercised in CI."""

    source_name = "languagetool"

    def __init__(self, language: str = "en-US") -> None:
        self._language = language
        self._tool = None

    def _ensure_tool(self):
        if self._tool is None:
            try:
                import language_tool_python
            except ImportError as exc:
                raise RuntimeError(
                    "language_tool_python is not installed — install the "
                    "apps/worker[languagetool] extra to enable grammar checking"
                ) from exc
            self._tool = language_tool_python.LanguageTool(self._language)
        return self._tool

    def check(self, text: str) -> list[GrammarError]:
        if not text.strip():
            return []
        tool = self._ensure_tool()
        matches = tool.check(text)
        return [
            GrammarError(
                category=match.category or "UNKNOWN",
                rule_id=match.ruleId,
                message=match.message,
                offset=match.offset,
                length=match.errorLength,
            )
            for match in matches
        ]
