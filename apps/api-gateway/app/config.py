from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# apps/api-gateway/app/config.py -> repo root is 3 parents up.
_REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    database_url: str = "postgresql+asyncpg://ielts:ielts@localhost:5432/ielts_speaking"
    redis_url: str = "redis://localhost:6379/0"

    jwt_secret: str = "dev-only-insecure-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_access_token_ttl_minutes: int = 60

    resume_token_ttl_hours: int = 24

    cors_origins: list[str] = ["http://localhost:3000"]

    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "ielts"
    s3_secret_key: str = "ielts-dev-secret"
    s3_bucket: str = "ielts-media"
    s3_region: str = "us-east-1"

    # Gemini Live (Spec 01 §4.1, §3). Model is config-driven, not hardcoded,
    # so a new Live model version is a deploy-time swap, not a client release.
    gemini_api_key: str = ""
    live_model_id: str = "models/gemini-2.0-flash-live-001"
    # Real endpoint by default; integration tests point this at a local
    # fake server so CI never dials out to Google (Spec 04 §3).
    gemini_live_ws_url: str = (
        "wss://generativelanguage.googleapis.com/ws/"
        "google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContent"
    )
    gemini_output_sample_rate_hz: int = 24000

    prompt_templates_dir: Path = _REPO_ROOT / "packages" / "prompt-templates"

    # Exam FSM (Spec 02 §3.3, §6.3, Spec 04 §2 Phase 3). Part 2's timers are
    # hard, non-negotiable exam-format durations in production; tests
    # override these to sub-second values so CI never waits on a real
    # 60s/120s clock (same pattern as gemini_live_ws_url being swapped for a
    # fixture server in tests).
    part2_prep_seconds: float = 60
    part2_long_turn_seconds: float = 120
    part2_long_turn_warn_at_seconds: float = 115

    # Soft phase exits (Spec 02 §1 "Soft target") are approximated by a
    # fixed completed-turn budget rather than real conversational-arc
    # detection (out of scope for Phase 3) — see Spec 04 build notes.
    intro_turns: int = 1
    part1_topic_turns: int = 4
    part2_roundoff_turns: int = 2
    part3_discussion_turns: int = 6
    reanchor_every_n_turns: int = 6

    finalizing_watchdog_seconds: float = 60

    # Grading pipeline trigger (Spec 03 §2.1) — a producer-only broker
    # connection, not a dependency on apps/worker's actual task code (see
    # app/services/grading_trigger.py).
    celery_broker_url: str = "amqp://ielts:ielts@localhost:5672//"

    # Internal/ops debug surface (Spec 04 §2 Phase 5) — not candidate-
    # facing, so protected by a shared token rather than candidate JWT auth.
    internal_debug_token: str = "dev-only-internal-debug-token-change-me"


settings = Settings()
