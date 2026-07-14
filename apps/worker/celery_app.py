import os

from celery import Celery

broker_url = os.environ.get("CELERY_BROKER_URL", "amqp://ielts:ielts@localhost:5672//")
result_backend = os.environ.get("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")

app = Celery("ielts_grading_engine", broker=broker_url, backend=result_backend)

app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    # Task modules are registered here as each pipeline stage lands
    # (Spec 04 Phase 5-7): finalize_media, transcribe_full_session landed
    # in Phase 5; compute_{fluency,lexical,grammar,pronunciation}_metrics
    # landed in Phase 6. synthesize_band_scores (Phase 7) is now wired as
    # grading_pipeline.py's chord callback over those four.
    include=[
        "tasks.media",
        "tasks.asr",
        "tasks.nlp.fluency",
        "tasks.nlp.lexical",
        "tasks.nlp.grammar",
        "tasks.pronunciation",
        "tasks.scoring",
        "pipelines.grading_pipeline",
    ],
    # Spec 03 §2.3 — asr/pronunciation/scoring are I/O-heavy (vendor calls)
    # and get dedicated pools sized independently of the lightweight media
    # stage; nlp is CPU-bound but fast (spaCy/LanguageTool), its own pool
    # too.
    task_routes={
        "tasks.media.finalize_media": {"queue": "media"},
        "tasks.media.sweep_expired_raw_audio": {"queue": "media"},
        "tasks.asr.transcribe_full_session": {"queue": "asr"},
        "tasks.nlp.*": {"queue": "nlp"},
        "tasks.pronunciation.*": {"queue": "pronunciation"},
        "tasks.scoring.*": {"queue": "scoring"},
    },
    # A crashed worker mid-task must not lose the job (Spec 03 §2.3).
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_retry_backoff=True,
)
