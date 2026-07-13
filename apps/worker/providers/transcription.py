"""Backline batch transcription providers (Spec 03 §3): Deepgram Nova
primary, self-hosted WhisperX fallback — both real vendor integrations.
Neither is exercised in CI (no API key, no ML models available in this
environment) — `tasks/asr.py` accepts an injectable provider so tests can
substitute a deterministic double without touching this module's real
vendor code paths.
"""
from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass
from typing import Protocol

import httpx

from config import settings

logger = logging.getLogger("worker.providers.transcription")


@dataclass(frozen=True)
class WordResult:
    word: str
    start_ms: int
    end_ms: int
    confidence: float
    source: str


class TranscriptionProvider(Protocol):
    def transcribe(self, audio_bytes: bytes, *, sample_rate: int) -> list[WordResult]: ...


class TranscriptionError(RuntimeError):
    """Raised when a provider cannot produce a transcript at all — the
    fallback chain in `transcribe_with_fallback` treats this the same as a
    low-confidence result and moves on to the next provider."""


class DeepgramTranscriptionProvider:
    """Real batch (prerecorded) call to Deepgram Nova (Spec 01 §3, Spec 03
    §3) — the primary vendor, run once against the finalized canonical
    audio rather than the live streaming tap, which is materially more
    accurate over a clean, non-network-jittered file."""

    def transcribe(self, audio_bytes: bytes, *, sample_rate: int) -> list[WordResult]:
        if not settings.deepgram_api_key:
            raise TranscriptionError("DEEPGRAM_API_KEY is not configured")

        try:
            response = httpx.post(
                settings.deepgram_api_url,
                params={
                    "model": settings.deepgram_model,
                    "punctuate": "true",
                    "smart_format": "true",
                },
                headers={
                    "Authorization": f"Token {settings.deepgram_api_key}",
                    "Content-Type": "audio/flac",
                },
                content=audio_bytes,
                timeout=settings.deepgram_timeout_s,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise TranscriptionError(f"Deepgram request failed: {exc}") from exc

        payload = response.json()
        try:
            alternative = payload["results"]["channels"][0]["alternatives"][0]
            raw_words = alternative["words"]
        except (KeyError, IndexError) as exc:
            raise TranscriptionError(f"unexpected Deepgram response shape: {exc}") from exc

        return [
            WordResult(
                word=w["word"],
                start_ms=round(w["start"] * 1000),
                end_ms=round(w["end"] * 1000),
                confidence=w["confidence"],
                source="deepgram",
            )
            for w in raw_words
        ]


class WhisperXTranscriptionProvider:
    """Self-hosted fallback (Spec 03 §3): Whisper large-v3 decoding +
    wav2vec2-based forced alignment for word-level timestamps. Heavy (torch
    + a multi-GB model download) — `whisperx` is an optional extra
    (`apps/worker[whisperx]`), lazy-imported here so a normal worker
    install never pulls it in."""

    def transcribe(self, audio_bytes: bytes, *, sample_rate: int) -> list[WordResult]:
        try:
            import whisperx
        except ImportError as exc:
            raise TranscriptionError(
                "whisperx is not installed — install the apps/worker[whisperx] extra "
                "to enable the fallback transcription path"
            ) from exc

        with tempfile.NamedTemporaryFile(suffix=".wav") as tmp:
            tmp.write(audio_bytes)
            tmp.flush()

            model = whisperx.load_model(
                settings.whisperx_model_name,
                settings.whisperx_device,
                language=settings.whisperx_language_code,
            )
            audio = whisperx.load_audio(tmp.name)
            result = model.transcribe(audio, batch_size=16)

            align_model, metadata = whisperx.load_align_model(
                language_code=result.get("language", settings.whisperx_language_code),
                device=settings.whisperx_device,
            )
            aligned = whisperx.align(
                result["segments"],
                align_model,
                metadata,
                audio,
                settings.whisperx_device,
                return_char_alignments=False,
            )

        words: list[WordResult] = []
        for segment in aligned["segments"]:
            for w in segment.get("words", []):
                if "start" not in w or "end" not in w:
                    # whisperx leaves timing unset for a small number of
                    # unalignable tokens — skipped rather than fabricating one.
                    continue
                words.append(
                    WordResult(
                        word=w["word"].strip(),
                        start_ms=round(w["start"] * 1000),
                        end_ms=round(w["end"] * 1000),
                        confidence=w.get("score", 0.0),
                        source="whisperx",
                    )
                )
        return words


def _average_confidence(words: list[WordResult]) -> float:
    if not words:
        return 0.0
    return sum(w.confidence for w in words) / len(words)


def transcribe_with_fallback(audio_bytes: bytes, *, sample_rate: int) -> list[WordResult]:
    """The confidence-gated fallback chain (Spec 03 §4.4's pattern, applied
    here to transcription instead of pronunciation scoring): Deepgram
    first, falling back to WhisperX on error, timeout, or a low average
    word confidence."""
    try:
        words = DeepgramTranscriptionProvider().transcribe(audio_bytes, sample_rate=sample_rate)
        if words and _average_confidence(words) >= settings.transcription_confidence_floor:
            return words
        logger.warning(
            "deepgram transcript below confidence floor (%.2f) or empty — "
            "falling back to whisperx",
            settings.transcription_confidence_floor,
        )
    except TranscriptionError:
        logger.warning("deepgram transcription failed — falling back to whisperx", exc_info=True)

    return WhisperXTranscriptionProvider().transcribe(audio_bytes, sample_rate=sample_rate)
