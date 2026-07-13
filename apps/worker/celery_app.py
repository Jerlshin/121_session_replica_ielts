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
    # (Spec 04 Phase 5-7): finalize_media, transcribe_full_session,
    # compute_{fluency,lexical,grammar,pronunciation}_metrics,
    # synthesize_band_scores.
    include=[],
)
