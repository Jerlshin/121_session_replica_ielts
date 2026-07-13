"""compute_pronunciation_scores golden-file test (Spec 03 §4.4, Spec 04 §2
Phase 6 / §3). No real recorded speech is used — Azure/GOP can't be
exercised in this environment (no API key, no ML models) — so this proves
two things for real instead: (1) the confidence-gated fallback-selection
*logic* itself, using fixture doubles standing in for "Azure returned a
low-confidence result" (Spec 03 §4.4's `score_pronunciation_segment`
pseudocode), and (2) the real librosa prosody proxy actually detecting
pitch on a synthesized tone — not mocked, genuinely computed.
"""
import io
import struct
import sys
import uuid
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "apps" / "api-gateway"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "apps" / "worker"))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services.media_tap import get_s3_client  # noqa: E402
from db import session_scope as worker_session_scope  # noqa: E402
from models import AudioSegment, FeatureVector, Transcript  # noqa: E402
from providers.pronunciation import (  # noqa: E402
    PronunciationAssessment,
    PronunciationProvider,
    PronunciationProviderError,
    assess_with_fallback,
    prosody_proxy,
)
from tasks.media import CANDIDATE_PCM_SAMPLE_RATE_HZ  # noqa: E402
from tasks.pronunciation import compute_pronunciation_scores  # noqa: E402


class FixturePronunciationProvider(PronunciationProvider):
    def __init__(self, source_name: str, assessment: PronunciationAssessment, *, error: bool = False):
        self.source_name = source_name
        self._assessment = assessment
        self._error = error
        self.called = False

    def assess(self, audio_bytes: bytes, reference_text: str, *, sample_rate: int):
        self.called = True
        if self._error:
            raise PronunciationProviderError("simulated vendor failure")
        return self._assessment


def _tone_pcm16(freq_hz: float, duration_s: float = 1.0, sample_rate: int = CANDIDATE_PCM_SAMPLE_RATE_HZ) -> bytes:
    t = np.linspace(0, duration_s, int(sample_rate * duration_s), endpoint=False)
    # Sweep the frequency a little so pyin has real pitch variation to
    # detect, rather than one perfectly flat tone.
    freq = freq_hz + 60 * (t / max(t[-1], 1e-9))
    y = 0.4 * np.sin(2 * np.pi * np.cumsum(freq) / sample_rate)
    return (y * 32767).astype("<i2").tobytes()


def _wav_bytes(pcm_bytes: bytes, sample_rate: int = CANDIDATE_PCM_SAMPLE_RATE_HZ) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm_bytes)
    return buf.getvalue()


# --- (1) real prosody extraction, no vendor involved -------------------


def test_prosody_proxy_detects_real_pitch_on_synthetic_tone():
    pcm = _tone_pcm16(180.0, duration_s=1.2)
    result = prosody_proxy(pcm, CANDIDATE_PCM_SAMPLE_RATE_HZ)

    assert result["low_snr_flag"] is False
    assert result["pitch_range_hz"] > 20  # the 60Hz sweep should be clearly detected


def test_prosody_proxy_flags_near_silent_audio_as_low_snr():
    silence = struct.pack(f"<{CANDIDATE_PCM_SAMPLE_RATE_HZ // 2}h", *([0] * (CANDIDATE_PCM_SAMPLE_RATE_HZ // 2)))
    result = prosody_proxy(silence, CANDIDATE_PCM_SAMPLE_RATE_HZ)
    assert result["low_snr_flag"] is True


# --- (2) confidence-gated fallback selection logic ----------------------


def test_fallback_uses_primary_when_confident():
    primary = FixturePronunciationProvider(
        "azure", PronunciationAssessment(90.0, 88.0, 95.0, 80.0, confidence=0.95)
    )
    fallback = FixturePronunciationProvider(
        "fallback_gop", PronunciationAssessment(1.0, 1.0, 1.0, 1.0, confidence=0.6)
    )
    result, source = assess_with_fallback(
        b"irrelevant", "hello world", sample_rate=16000, primary=primary, fallback=fallback
    )
    assert source == "azure"
    assert result.accuracy == 90.0
    assert fallback.called is False


def test_fallback_triggers_on_low_confidence():
    primary = FixturePronunciationProvider(
        "azure", PronunciationAssessment(90.0, 88.0, 95.0, 80.0, confidence=0.2)
    )
    fallback = FixturePronunciationProvider(
        "fallback_gop", PronunciationAssessment(55.0, 55.0, 100.0, 0.0, confidence=0.6)
    )
    result, source = assess_with_fallback(
        b"irrelevant", "hello world", sample_rate=16000, primary=primary, fallback=fallback
    )
    assert source == "fallback_gop"
    assert result.accuracy == 55.0
    assert fallback.called is True


def test_fallback_triggers_on_low_snr_even_with_high_confidence():
    primary = FixturePronunciationProvider(
        "azure", PronunciationAssessment(90.0, 88.0, 95.0, 80.0, confidence=0.99)
    )
    fallback = FixturePronunciationProvider(
        "fallback_gop", PronunciationAssessment(40.0, 40.0, 100.0, 0.0, confidence=0.6)
    )
    _result, source = assess_with_fallback(
        b"irrelevant",
        "hello world",
        sample_rate=16000,
        low_snr_flag=True,
        primary=primary,
        fallback=fallback,
    )
    assert source == "fallback_gop"


def test_fallback_triggers_on_primary_error():
    primary = FixturePronunciationProvider(
        "azure", PronunciationAssessment(0, 0, 0, 0, confidence=0), error=True
    )
    fallback = FixturePronunciationProvider(
        "fallback_gop", PronunciationAssessment(60.0, 60.0, 100.0, 0.0, confidence=0.6)
    )
    result, source = assess_with_fallback(
        b"irrelevant", "hello world", sample_rate=16000, primary=primary, fallback=fallback
    )
    assert source == "fallback_gop"
    assert result.accuracy == 60.0


# --- (3) the full task against real Postgres + MinIO --------------------


def _create_session() -> str:
    with TestClient(app) as client:
        login = client.post(
            "/auth/login", json={"email": "pronunciation-golden@example.com", "full_name": "Pron Tester"}
        )
        token = login.json()["access_token"]
        return client.post("/sessions", headers={"Authorization": f"Bearer {token}"}).json()["id"]


def test_task_writes_feature_vectors_with_real_prosody_and_fixture_provenance():
    session_id = _create_session()
    session_uuid = uuid.UUID(session_id)
    turn_id = uuid.uuid4()

    pcm = _tone_pcm16(160.0, duration_s=1.0)
    wav = _wav_bytes(pcm)
    storage_key = f"raw-audio/{session_id}/segments/{turn_id}_1.wav"
    get_s3_client().put_object(Bucket="ielts-media", Key=storage_key, Body=wav, ContentType="audio/wav")

    with worker_session_scope() as db:
        db.add(
            AudioSegment(
                session_id=session_uuid,
                turn_id=turn_id,
                seq=1,
                storage_key=storage_key,
                checksum="fixture-checksum",
                byte_size=len(wav),
                exam_phase="PART2_LONG_TURN",
            )
        )
        db.add(
            Transcript(
                session_id=session_uuid,
                turn_id=turn_id,
                seq=0,
                word="hello",
                start_ms=0,
                end_ms=500,
                confidence=0.9,
                speaker="candidate",
                source="fixture",
            )
        )

    provider = FixturePronunciationProvider(
        "fixture", PronunciationAssessment(70.0, 72.0, 100.0, 65.0, confidence=0.8)
    )
    result = compute_pronunciation_scores(session_id, provider=provider)

    assert set(result.keys()) == {"part1", "part2", "part3", "session"}
    part2 = result["part2"]
    assert part2["accuracy"] == 70.0
    assert part2["segment_count"] == 1
    # Real prosody, genuinely computed by librosa on the synthetic tone —
    # not something the fixture provider supplies.
    assert part2["pitch_range_hz"] > 0
    assert part2["provenance"]["segments"][0]["source"] == "fixture"

    with worker_session_scope() as db:
        rows = (
            db.query(FeatureVector)
            .filter_by(session_id=session_uuid, criterion="pronunciation")
            .all()
        )
        assert {r.phase for r in rows} == {"part1", "part2", "part3", "session"}
        part1_row = next(r for r in rows if r.phase == "part1")
        assert part1_row.features["segment_count"] == 0
