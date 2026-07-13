import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from celery_app import app  # noqa: E402


def test_celery_app_configured():
    assert app.main == "ielts_grading_engine"
    assert app.conf.task_serializer == "json"
