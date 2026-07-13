"""Enqueues the grading pipeline root task on exam completion (Spec 03
§2.1: the COMPLETE FSM event enqueues a single root job). A lightweight,
producer-only Celery client — this deliberately does NOT import
apps/worker's task modules (which pull in worker-side dependencies like
spaCy/whisperx in later phases); it only needs the broker URL and the
task's stable name to enqueue by name, Celery's standard cross-service
producer pattern. Sending is a blocking AMQP call, so the caller
(exam_orchestrator.py) is expected to run this off the event loop via
run_in_threadpool.
"""
import logging
import uuid

from celery import Celery

from app.config import settings

logger = logging.getLogger("app.grading_trigger")

GRADE_EXAM_SESSION_TASK_NAME = "grading.grade_exam_session"

_producer = Celery(broker=settings.celery_broker_url)


def enqueue_grading(session_id: uuid.UUID) -> None:
    _producer.send_task(GRADE_EXAM_SESSION_TASK_NAME, kwargs={"session_id": str(session_id)})
