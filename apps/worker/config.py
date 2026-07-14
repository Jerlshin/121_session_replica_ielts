from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# apps/worker/config.py -> repo root is 2 parents up.
_REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    # Sync driver (psycopg2) — Celery tasks run outside an event loop, so
    # the worker deliberately does not share api-gateway's async engine.
    # Same DATABASE_URL env var as api-gateway; the +asyncpg -> +psycopg2
    # normalization happens in db.py so both apps can read one env var.
    database_url: str = "postgresql+asyncpg://ielts:ielts@localhost:5432/ielts_speaking"

    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "ielts"
    s3_secret_key: str = "ielts-dev-secret"
    s3_bucket: str = "ielts-media"
    s3_region: str = "us-east-1"
    # Spec 01 §7 illustrative retention window for raw-video/ ("e.g. 90
    # days, configurable per institution contract") — enforced as a real
    # bucket lifecycle rule (Spec 04 §2 Phase 8), not just documentation.
    raw_video_retention_days: int = 90

    # Backline transcription (Spec 03 §3). Primary vendor; empty by default
    # so a missing key fails loudly rather than silently no-op-ing.
    deepgram_api_key: str = ""
    deepgram_api_url: str = "https://api.deepgram.com/v1/listen"
    deepgram_model: str = "nova-2"
    deepgram_timeout_s: float = 120.0

    # Confidence-gated fallback threshold (Spec 03 §4.4's pattern, applied
    # to transcription): below this average word confidence, or on a
    # primary-vendor error/timeout, fall back to self-hosted WhisperX.
    transcription_confidence_floor: float = 0.7

    whisperx_model_name: str = "large-v3"
    whisperx_device: str = "cpu"
    whisperx_language_code: str = "en"

    # Pronunciation (Spec 03 §4.4). Primary vendor; empty by default so a
    # missing key fails loudly rather than silently no-op-ing.
    azure_speech_key: str = ""
    azure_speech_region: str = "eastus"
    azure_speech_timeout_s: float = 60.0

    # Confidence-gated fallback threshold (Spec 03 §4.4's pattern): below
    # this confidence, or on a primary-vendor error/timeout/low-SNR
    # segment, fall back to the self-hosted GOP scorer.
    pronunciation_confidence_floor: float = 0.7

    gop_model_name: str = "facebook/wav2vec2-lv-60-espeak-cv-ft"

    # LLM Rubric Judge (Spec 03 §5). Primary and, per §5.2, the default
    # production ScoringLLM implementation; empty key fails loudly rather
    # than silently no-op-ing.
    openai_api_key: str = ""
    scoring_llm_model: str = "gpt-5.1"

    # Spec 01 §7 / Spec 03 §5.1: the licensed band-descriptor asset is
    # injected via the secret store at deploy time and is deliberately
    # never committed to this repo (packages/grading-rubric-assets/ holds
    # only a .gitkeep) — this just names where to look for it.
    rubric_assets_dir: Path = _REPO_ROOT / "packages" / "grading-rubric-assets"

    # Spec 03 §5.6: >1.0 band disagreement between the two judge passes on
    # any single criterion routes the session to human review rather than
    # being auto-resolved by averaging.
    self_consistency_band_disagreement_threshold: float = 1.0


settings = Settings()
