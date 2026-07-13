"""Pronunciation scoring (Spec 03 §4.4): unscripted-mode assessment — the
backline ASR transcript stands in as the reference text, since there is no
fixed script. Real Azure AI Speech Pronunciation Assessment as the primary
vendor, a real self-hosted wav2vec2-CTC Goodness-of-Pronunciation (GOP)
scorer as the fallback, both behind a `PronunciationProvider` interface —
same posture as Deepgram/WhisperX (Phase 5) and LanguageTool. The prosody
proxy is computed locally via librosa regardless of which vendor path
runs, since neither vendor is a hard requirement for that part.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

import httpx
import numpy as np

from config import settings

logger = logging.getLogger("worker.providers.pronunciation")


@dataclass(frozen=True)
class PronunciationAssessment:
    accuracy: float  # 0-100
    fluency: float  # 0-100
    completeness: float  # 0-100
    prosody: float  # 0-100
    confidence: float  # 0.0-1.0


class PronunciationProvider(Protocol):
    source_name: str

    def assess(
        self, audio_bytes: bytes, reference_text: str, *, sample_rate: int
    ) -> PronunciationAssessment: ...


class PronunciationProviderError(RuntimeError):
    """Raised when a provider cannot produce an assessment at all — the
    fallback chain in `assess_with_fallback` treats this the same as a
    low-confidence result."""


def _pcm16_bytes_to_float_array(pcm_bytes: bytes) -> np.ndarray:
    return np.frombuffer(pcm_bytes, dtype="<i2").astype(np.float32) / 32768.0


class AzurePronunciationProvider:
    """Real Azure AI Speech Pronunciation Assessment integration (Spec 03
    §4.4) — lazy in the sense that it only actually calls out when
    `AZURE_SPEECH_KEY` is configured; never exercised in CI."""

    source_name = "azure"

    def assess(
        self, audio_bytes: bytes, reference_text: str, *, sample_rate: int
    ) -> PronunciationAssessment:
        if not settings.azure_speech_key:
            raise PronunciationProviderError("AZURE_SPEECH_KEY is not configured")

        import base64
        import json

        pronunciation_config = base64.b64encode(
            json.dumps(
                {
                    "ReferenceText": reference_text,
                    "GradingSystem": "HundredMark",
                    "Granularity": "Phoneme",
                    "EnableProsodyAssessment": True,
                }
            ).encode("utf-8")
        ).decode("ascii")

        url = (
            f"https://{settings.azure_speech_region}.stt.speech.microsoft.com/"
            "speech/recognition/conversation/cognitiveservices/v1"
        )
        try:
            response = httpx.post(
                url,
                params={"language": "en-US", "format": "detailed"},
                headers={
                    "Ocp-Apim-Subscription-Key": settings.azure_speech_key,
                    "Content-Type": f"audio/wav; codecs=audio/pcm; samplerate={sample_rate}",
                    "Pronunciation-Assessment": pronunciation_config,
                    "Accept": "application/json",
                },
                content=audio_bytes,
                timeout=settings.azure_speech_timeout_s,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise PronunciationProviderError(f"Azure pronunciation request failed: {exc}") from exc

        payload = response.json()
        try:
            assessment = payload["NBest"][0]["PronunciationAssessment"]
        except (KeyError, IndexError) as exc:
            raise PronunciationProviderError(f"unexpected Azure response shape: {exc}") from exc

        return PronunciationAssessment(
            accuracy=float(assessment["AccuracyScore"]),
            fluency=float(assessment["FluencyScore"]),
            completeness=float(assessment["CompletenessScore"]),
            # Prosody scoring is a newer Azure API addition — fall back to
            # the fluency score as a proxy if this API version omits it.
            prosody=float(assessment.get("ProsodyScore", assessment["FluencyScore"])),
            confidence=float(payload.get("Confidence", 0.9)),
        )


class GOPFallbackProvider:
    """Self-hosted Goodness-of-Pronunciation fallback (Spec 03 §4.4):

        GOP(phone p, frames F) = (1/|F|) * sum_t log(P(p|x_t) / max_q P(q|x_t))

    — a phone's posterior probability relative to the best-competing phone
    at each aligned frame, averaged over the phone's duration (the
    standard Witt & Young-style formula). `torchaudio.functional.
    forced_align` supplies the frame -> reference-token assignment the
    formula needs (not derivable from the model's own argmax alone — that
    would trivially always compare the top phone against itself). Heavy
    (torch + a wav2vec2 checkpoint) — `apps/worker[gop]` optional extra,
    lazy-imported, never exercised in CI.
    """

    source_name = "fallback_gop"

    def __init__(self, model_name: str | None = None) -> None:
        self._model_name = model_name or settings.gop_model_name
        self._model = None
        self._processor = None

    def _ensure_model(self):
        if self._model is None:
            try:
                from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor
            except ImportError as exc:
                raise RuntimeError(
                    "transformers/torch are not installed — install the "
                    "apps/worker[gop] extra to enable the GOP fallback"
                ) from exc
            self._processor = Wav2Vec2Processor.from_pretrained(self._model_name)
            self._model = Wav2Vec2ForCTC.from_pretrained(self._model_name)
            self._model.eval()
        return self._model, self._processor

    def assess(
        self, audio_bytes: bytes, reference_text: str, *, sample_rate: int
    ) -> PronunciationAssessment:
        import torch
        import torchaudio

        model, processor = self._ensure_model()
        samples = _pcm16_bytes_to_float_array(audio_bytes)

        inputs = processor(samples, sampling_rate=sample_rate, return_tensors="pt")
        with torch.no_grad():
            logits = model(inputs.input_values).logits  # (1, T, V)
        log_probs = torch.log_softmax(logits, dim=-1)

        with processor.as_target_processor():
            target_ids = processor(reference_text.lower()).input_ids
        targets = torch.tensor([target_ids], dtype=torch.long)

        aligned_tokens, _alignment_scores = torchaudio.functional.forced_align(
            log_probs, targets, blank=processor.tokenizer.pad_token_id
        )

        max_log_probs = log_probs[0].max(dim=-1).values
        frame_tokens = aligned_tokens[0].tolist()
        per_frame_gop = [
            (log_probs[0, t, token_id] - max_log_probs[t]).item()
            for t, token_id in enumerate(frame_tokens)
            if token_id != processor.tokenizer.pad_token_id
        ]
        mean_gop = sum(per_frame_gop) / len(per_frame_gop) if per_frame_gop else 0.0

        # GOP is always <= 0 (a phone's posterior is never above the
        # frame's argmax); map onto the same 0-100 scale Azure reports so
        # the two providers' outputs are comparable at the feature-vector
        # level, with `source` as the audit trail for which produced it
        # (Spec 03 §4.4's explicit provenance requirement).
        accuracy = max(0.0, min(100.0, 100.0 * (1.0 + mean_gop / 5.0)))

        return PronunciationAssessment(
            accuracy=round(accuracy, 1),
            # GOP has no native fluency/completeness sub-score of its own
            # (Spec 03 §4.4: "the fallback path has no native prosody
            # sub-score of its own" — extends to fluency/completeness too);
            # accuracy is reused as the best available proxy rather than
            # fabricating an independent number.
            fluency=round(accuracy, 1),
            completeness=100.0,
            prosody=0.0,  # real prosody comes from the local librosa proxy, not this provider
            confidence=0.6,  # fixed, conservative — GOP is the fallback path, no vendor-reported figure
        )


def prosody_proxy(audio_bytes: bytes, sample_rate: int) -> dict:
    """Pitch range + stress-timing regularity via F0/energy contour
    extraction (Spec 03 §4.4) — real librosa, no vendor needed, so this
    runs identically regardless of which fallback path scored the
    segment. Also derives a coarse low-SNR flag, the other half of Spec
    03 §4.4's `segment.low_snr_flag` gating condition."""
    import librosa

    samples = _pcm16_bytes_to_float_array(audio_bytes)
    if len(samples) < sample_rate * 0.05:
        return {"pitch_range_hz": 0.0, "stress_timing_regularity": 0.0, "low_snr_flag": True}

    f0, _voiced_flag, _voiced_probs = librosa.pyin(
        samples, fmin=librosa.note_to_hz("C2"), fmax=librosa.note_to_hz("C7"), sr=sample_rate
    )
    voiced_f0 = f0[~np.isnan(f0)] if f0 is not None else np.array([])
    pitch_range_hz = float(voiced_f0.max() - voiced_f0.min()) if len(voiced_f0) > 1 else 0.0

    rms = librosa.feature.rms(y=samples)[0]
    stress_timing_regularity = float(1.0 / (1.0 + np.std(rms))) if len(rms) > 1 else 0.0
    low_snr_flag = bool(np.mean(rms) < 1e-4) if len(rms) else True

    return {
        "pitch_range_hz": round(pitch_range_hz, 1),
        "stress_timing_regularity": round(stress_timing_regularity, 3),
        "low_snr_flag": low_snr_flag,
    }


def assess_with_fallback(
    audio_bytes: bytes,
    reference_text: str,
    *,
    sample_rate: int,
    low_snr_flag: bool = False,
    primary: PronunciationProvider | None = None,
    fallback: PronunciationProvider | None = None,
) -> tuple[PronunciationAssessment, str]:
    """The confidence-gated fallback chain from Spec 03 §4.4's
    `score_pronunciation_segment` pseudocode, transcribed near-verbatim:
    the primary provider first, falling back on error/timeout, low
    confidence, or a low-SNR segment. `primary`/`fallback` default to the
    real Azure/GOP providers but are injectable so the *gating logic*
    itself is directly testable with fixture doubles standing in for
    "Azure returned a low-confidence result" — neither real vendor can be
    exercised in this environment."""
    primary_provider = primary or AzurePronunciationProvider()
    fallback_provider = fallback or GOPFallbackProvider()

    try:
        result = primary_provider.assess(audio_bytes, reference_text, sample_rate=sample_rate)
        if result.confidence >= settings.pronunciation_confidence_floor and not low_snr_flag:
            return result, primary_provider.source_name
        logger.warning(
            "primary pronunciation result below confidence floor (%.2f) or low-SNR segment — "
            "falling back",
            settings.pronunciation_confidence_floor,
        )
    except PronunciationProviderError:
        logger.warning("primary pronunciation assessment failed — falling back", exc_info=True)

    fallback_result = fallback_provider.assess(audio_bytes, reference_text, sample_rate=sample_rate)
    return fallback_result, fallback_provider.source_name
