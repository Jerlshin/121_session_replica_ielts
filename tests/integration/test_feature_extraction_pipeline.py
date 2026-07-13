"""Phase 6 exit criterion (Spec 04 §2): "running all four [feature
extraction] tasks against a real Phase-5 transcript produces plausible,
internally consistent FeatureVector JSON with correct provenance tags."

Drives the *real* Phase 5 pipeline (finalize_media, then
transcribe_full_session — both actual tasks, not stand-ins) against seeded
per-turn audio spanning Part 1/2/3, then runs all four Phase 6
compute_* tasks against the resulting real `transcripts` rows. Only the
ASR/grammar/pronunciation *vendor* calls are fixtures (no real Deepgram/
LanguageTool/Azure/GOP is available in this environment) — everything
else (stitching, DB persistence, phase attribution, feature computation)
is exercised for real.
"""
import io
import struct
import sys
import uuid
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "apps" / "api-gateway"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "apps" / "worker"))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services.media_tap import get_s3_client  # noqa: E402
from db import session_scope as worker_session_scope  # noqa: E402
from models import AudioSegment, FeatureVector  # noqa: E402
from providers.grammar_check import GrammarCheckProvider  # noqa: E402
from providers.pronunciation import PronunciationAssessment, PronunciationProvider  # noqa: E402
from providers.transcription import TranscriptionProvider, WordResult  # noqa: E402
from tasks.asr import transcribe_full_session  # noqa: E402
from tasks.media import CANDIDATE_PCM_SAMPLE_RATE_HZ, finalize_media  # noqa: E402
from tasks.nlp.fluency import compute_fluency_metrics  # noqa: E402
from tasks.nlp.grammar import compute_grammar_metrics  # noqa: E402
from tasks.nlp.lexical import compute_lexical_metrics  # noqa: E402
from tasks.pronunciation import compute_pronunciation_scores  # noqa: E402

# One short, plausible English response per part — real content so
# lexical/grammar/fluency compute something meaningful, not just zeros.
TURN_TEXTS = {
    "PART1_TOPIC_A": "I really enjoy reading books in the evening because it helps me relax",
    "PART2_LONG_TURN": (
        "I want to talk about a skill I learned recently which was cooking "
        "and although it was difficult at first I gradually improved"
    ),
    "PART3_DISCUSSION": "In my opinion technology has changed how people communicate significantly",
}


class GrammarCheckFixtureProvider(GrammarCheckProvider):
    source_name = "fixture"

    def check(self, text: str) -> list:
        return []


class PronunciationFixtureProvider(PronunciationProvider):
    source_name = "fixture"

    def assess(self, audio_bytes: bytes, reference_text: str, *, sample_rate: int):
        return PronunciationAssessment(75.0, 78.0, 100.0, 70.0, confidence=0.85)


def _wav_bytes(num_samples: int, sample_rate: int = CANDIDATE_PCM_SAMPLE_RATE_HZ) -> tuple[bytes, bytes]:
    samples = [int(1500 * ((i % 32) / 32.0 - 0.5)) for i in range(num_samples)]
    pcm = struct.pack(f"<{num_samples}h", *samples)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm)
    return buf.getvalue(), pcm


def test_all_four_criteria_produce_consistent_feature_vectors_from_a_real_transcript():
    with TestClient(app) as client:
        login = client.post(
            "/auth/login",
            json={"email": "feature-pipeline@example.com", "full_name": "Pipeline Tester"},
        )
        token = login.json()["access_token"]
        session_id = client.post(
            "/sessions", headers={"Authorization": f"Bearer {token}"}
        ).json()["id"]

    session_uuid = uuid.UUID(session_id)
    s3 = get_s3_client()
    turn_ids: dict[str, uuid.UUID] = {}

    with worker_session_scope() as db:
        for phase, text in TURN_TEXTS.items():
            turn_id = uuid.uuid4()
            turn_ids[phase] = turn_id
            wav_bytes, _pcm = _wav_bytes(num_samples=8000)  # 0.5s of audio per turn
            key = f"raw-audio/{session_id}/segments/{turn_id}_1.wav"
            s3.put_object(Bucket="ielts-media", Key=key, Body=wav_bytes, ContentType="audio/wav")
            db.add(
                AudioSegment(
                    session_id=session_uuid,
                    turn_id=turn_id,
                    seq=1,
                    storage_key=key,
                    checksum="fixture-checksum",
                    byte_size=len(wav_bytes),
                    exam_phase=phase,
                )
            )

    # Stage 1 (real Phase 5 task): stitch the three turns into one
    # canonical file and record their time offsets.
    finalize_result = finalize_media(session_id)
    turn_boundaries = finalize_result["turn_boundaries"]
    assert len(turn_boundaries) == 3

    # Stage 2 (real Phase 5 task): transcribe the canonical file. Only the
    # ASR *vendor* is a fixture — word timing is derived from
    # finalize_media's real turn_boundaries, and persistence goes through
    # the real transcribe_full_session task.
    boundary_by_turn = {b["turn_id"]: b for b in turn_boundaries}

    class FixtureTranscriptionProvider(TranscriptionProvider):
        def transcribe(self, audio_bytes: bytes, *, sample_rate: int) -> list[WordResult]:
            words = []
            for phase, turn_id in turn_ids.items():
                boundary = boundary_by_turn[str(turn_id)]
                t = boundary["start_ms"] + 10
                for word in TURN_TEXTS[phase].split():
                    words.append(
                        WordResult(word=word, start_ms=t, end_ms=t + 150, confidence=0.9, source="fixture")
                    )
                    t += 180
            return words

    transcribe_result = transcribe_full_session(
        session_id, provider=FixtureTranscriptionProvider()
    )
    assert transcribe_result["word_count"] == sum(len(t.split()) for t in TURN_TEXTS.values())

    # Stage 3 (Phase 6 — this phase's actual deliverable): all four
    # feature-extraction tasks against the real transcripts table.
    fluency = compute_fluency_metrics(session_id)
    lexical = compute_lexical_metrics(session_id, provider=GrammarCheckFixtureProvider())
    grammar = compute_grammar_metrics(session_id, provider=GrammarCheckFixtureProvider())
    pronunciation = compute_pronunciation_scores(session_id, provider=PronunciationFixtureProvider())

    for result in (fluency, lexical, grammar, pronunciation):
        assert set(result.keys()) == {"part1", "part2", "part3", "session"}

    # Plausibility + internal consistency: each phase that actually had a
    # turn shows non-zero activity, and the session aggregate reflects the
    # sum of all three phases.
    for phase in ("part1", "part2", "part3"):
        assert fluency[phase]["total_words"] > 0
        assert lexical[phase]["total_words"] > 0
        assert grammar[phase]["total_words"] > 0
        assert pronunciation[phase]["segment_count"] == 1

    total_words_by_phase = sum(fluency[p]["total_words"] for p in ("part1", "part2", "part3"))
    assert fluency["session"]["total_words"] == total_words_by_phase
    assert pronunciation["session"]["segment_count"] == 3

    # Provenance: every written feature_vectors row names a source.
    with worker_session_scope() as db:
        rows = db.query(FeatureVector).filter_by(session_id=session_uuid).all()
        assert len(rows) == 4 * 4  # 4 criteria x (part1, part2, part3, session)
        by_criterion: dict[str, set[str]] = {}
        for row in rows:
            by_criterion.setdefault(row.criterion, set()).add(row.phase)
            if row.criterion == "pronunciation":
                provenance = row.features["provenance"]["segments"]
                assert all(s["source"] == "fixture" for s in provenance)
            else:
                assert row.features["provenance"]["source"] in ("rule_based", "fixture")
        assert by_criterion == {
            "fluency": {"part1", "part2", "part3", "session"},
            "lexical": {"part1", "part2", "part3", "session"},
            "grammar": {"part1", "part2", "part3", "session"},
            "pronunciation": {"part1", "part2", "part3", "session"},
        }
