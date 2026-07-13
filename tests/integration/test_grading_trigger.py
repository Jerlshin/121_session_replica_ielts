"""grading_trigger.enqueue_grading (Spec 03 §2.1; Spec 04 §2 Phase 5)
against a real RabbitMQ broker — proves the gateway's producer-only Celery
client actually publishes a task message by name, not just that the call
doesn't raise.
"""
import sys
import uuid
from pathlib import Path
from queue import Empty

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "apps" / "api-gateway"))

from kombu import Connection  # noqa: E402

from app.config import settings  # noqa: E402
from app.services.grading_trigger import GRADE_EXAM_SESSION_TASK_NAME, enqueue_grading  # noqa: E402


def test_enqueue_grading_publishes_a_real_task_message():
    session_id = uuid.uuid4()

    with Connection(settings.celery_broker_url) as conn:
        with conn.SimpleQueue("celery") as queue:
            # The default "celery" queue is shared with other integration
            # tests whose sessions reach COMPLETE (e.g.
            # test_full_exam_session_flow.py) and therefore also enqueue a
            # real grading job — drain whatever's already sitting there
            # first so this test only asserts on the message it publishes.
            _drain(queue)

            enqueue_grading(session_id)

            message = queue.get(timeout=5)
            args, kwargs, _embed = message.payload
            assert args == []
            assert kwargs == {"session_id": str(session_id)}
            assert message.headers["task"] == GRADE_EXAM_SESSION_TASK_NAME
            message.ack()


def _drain(q) -> None:
    while True:
        try:
            message = q.get(block=False)
        except Empty:
            return
        message.ack()
