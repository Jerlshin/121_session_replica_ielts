import uuid

from app.config import settings
from app.services.media_tap import ensure_bucket, get_s3_client

# Spec 01 §8: presigned URLs must carry strict TTL bounds. The browser
# uploads the proctoring clip immediately after PTT release/session close
# (see apps/web's ProctoringRecorder.stop() -> uploadVideoBlob call), so
# there's no legitimate reason this URL should still be usable an hour
# later — 10 minutes covers real network slowness without leaving a
# long-lived credential-equivalent sitting in the client.
#
# Note: `generate_presigned_url("put_object", ...)` can only scope
# `Bucket`/`Key`/`ContentType`, not a `ContentLengthRange` condition —
# that requires switching to `generate_presigned_post`, a bigger change to
# the upload flow (apps/web's `uploadVideoBlob` does a plain `fetch(...,
# {method: "PUT"})`) that's out of proportion for this hardening pass.
# TTL tightening plus the already-enforced `ContentType` condition is the
# concrete improvement landing here; a size-bound condition is a
# documented follow-up if a max-clip-size policy is ever needed.
VIDEO_UPLOAD_TTL_SECONDS = 600


def create_video_upload_url(session_id: uuid.UUID) -> dict:
    """Presigned PUT URL for the proctoring video track — uploaded browser
    -> object storage directly, never proxied through an API pod
    (CLAUDE.md rule 3, Spec 01 §3.1/§5.5)."""
    storage_key = f"raw-video/{session_id}/full.webm"
    client = get_s3_client()
    ensure_bucket(client)
    url = client.generate_presigned_url(
        "put_object",
        Params={"Bucket": settings.s3_bucket, "Key": storage_key, "ContentType": "video/webm"},
        ExpiresIn=VIDEO_UPLOAD_TTL_SECONDS,
    )
    return {"upload_url": url, "storage_key": storage_key}
