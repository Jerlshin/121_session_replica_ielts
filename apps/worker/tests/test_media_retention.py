"""Unit test for the data-minimization sweep's actual deletion logic (Spec
01 §7/§8, Spec 04 §2 Phase 8) — a fake S3 client, no real MinIO/DB/broker
involved. `tasks.media._delete_raw_audio_segments` is the pure-ish helper
`sweep_expired_raw_audio` wraps with job-status bookkeeping; testing it
directly is what lets this stay a fast unit test rather than needing the
full Celery task + Postgres. The real-MinIO, full-task version of this
test lives at tests/integration/test_media_retention_sweep.py.
"""
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tasks.media import _delete_raw_audio_segments  # noqa: E402


class _FakePaginator:
    def __init__(self, client: "_FakeS3Client") -> None:
        self._client = client

    def paginate(self, *, Bucket: str, Prefix: str):
        matching = sorted(key for key in self._client.keys if key.startswith(Prefix))
        # A single page is enough to exercise the pagination loop's shape
        # without needing to fabricate a real multi-page continuation token.
        yield {"Contents": [{"Key": key} for key in matching]}


class _FakeS3Client:
    def __init__(self, keys: set[str]) -> None:
        self.keys = set(keys)
        self.delete_calls: list[list[str]] = []

    def get_paginator(self, operation_name: str) -> _FakePaginator:
        assert operation_name == "list_objects_v2"
        return _FakePaginator(self)

    def delete_objects(self, *, Bucket: str, Delete: dict) -> None:
        deleted = [obj["Key"] for obj in Delete["Objects"]]
        self.delete_calls.append(deleted)
        for key in deleted:
            self.keys.discard(key)


def test_sweep_deletes_only_segment_keys_and_keeps_canonical_flac():
    session_id = str(uuid.uuid4())
    segment_keys = {
        f"raw-audio/{session_id}/segments/turn1_1.wav",
        f"raw-audio/{session_id}/segments/turn2_1.wav",
    }
    canonical_key = f"raw-audio/{session_id}/canonical.flac"
    client = _FakeS3Client(segment_keys | {canonical_key})

    result = _delete_raw_audio_segments(client, session_id)

    assert result["deleted_count"] == 2
    assert client.keys == {canonical_key}


def test_sweep_only_touches_the_given_session_prefix():
    session_id = str(uuid.uuid4())
    other_session_id = str(uuid.uuid4())
    own_key = f"raw-audio/{session_id}/segments/turn1_1.wav"
    other_key = f"raw-audio/{other_session_id}/segments/turn1_1.wav"
    client = _FakeS3Client({own_key, other_key})

    result = _delete_raw_audio_segments(client, session_id)

    assert result["deleted_count"] == 1
    assert client.keys == {other_key}


def test_sweep_is_a_no_op_when_no_segments_exist():
    session_id = str(uuid.uuid4())
    canonical_key = f"raw-audio/{session_id}/canonical.flac"
    client = _FakeS3Client({canonical_key})

    result = _delete_raw_audio_segments(client, session_id)

    assert result["deleted_count"] == 0
    assert client.keys == {canonical_key}
    assert client.delete_calls == []
