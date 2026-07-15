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
    # Spec 01 §7 illustrative retention window for raw-video/ ("e.g. 90
    # days, configurable per institution contract") — enforced as a real
    # bucket lifecycle rule (Spec 04 §2 Phase 8), not just documentation.
    raw_video_retention_days: int = 90

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

    # Part 1's real-exam "4-5 minutes combined" window (Spec 02 §1/§4),
    # enforced as a hard floor+ceiling spanning PART1_TOPIC_A/B/C: the
    # ceiling watchdog force-advances to Part 2 if still running at
    # part1_max_seconds; the floor gates the final topic's own exit so the
    # candidate can't leave Part 1 before part1_min_seconds has elapsed.
    part1_min_seconds: float = 240
    part1_max_seconds: float = 300

    # Part 3's own "4-5 minute" band (Spec 02 §1/§4), used as the clamp
    # range for the dynamically-computed remainder-of-total budget below.
    part3_min_seconds: float = 240
    part3_max_seconds: float = 300

    # The real exam's overall 11-14 minute Speaking-section threshold (Spec
    # 02 §4): Part 3's dynamic ceiling is computed as "whatever's left of
    # exam_total_max_seconds", clamped into [part3_min_seconds,
    # part3_max_seconds] — see exam_orchestrator._on_enter_phase.
    exam_total_min_seconds: float = 660
    exam_total_max_seconds: float = 840

    # Soft phase exits (Spec 02 §1 "Soft target") are approximated by a
    # fixed completed-turn budget rather than real conversational-arc
    # detection (out of scope for Phase 3) — see Spec 04 build notes.
    intro_turns: int = 1
    part1_topic_turns: int = 4
    # Spec 02 §3.4: after either a voluntary release or the 120s hard
    # cutoff, the examiner asks exactly one optional round-off question
    # before moving to Part 3 — not a new topic, just a brief wrap-up.
    part2_roundoff_turns: int = 1
    part3_discussion_turns: int = 6
    reanchor_every_n_turns: int = 6

    # Safety net for the directive dispatcher's turn-taking gate
    # (exam_orchestrator._dispatch_directives): normally a queued directive
    # waits for Gemini's TurnComplete on whatever's currently in flight, but
    # a candidate barge-in (Interrupted, Spec 01 §4.1) can abort a turn
    # without Gemini ever emitting one for it. Rather than risk the whole
    # exam stalling on a TurnComplete that will never arrive, the dispatcher
    # gives up waiting after this long and sends anyway.
    directive_dispatch_timeout_seconds: float = 10.0

    finalizing_watchdog_seconds: float = 60

    # Grading pipeline trigger (Spec 03 §2.1) — a producer-only broker
    # connection, not a dependency on apps/worker's actual task code (see
    # app/services/grading_trigger.py).
    celery_broker_url: str = "amqp://ielts:ielts@localhost:5672//"

    # Internal/ops debug surface (Spec 04 §2 Phase 5) — not candidate-
    # facing, so protected by a shared token rather than candidate JWT auth.
    internal_debug_token: str = "dev-only-internal-debug-token-change-me"


settings = Settings()
