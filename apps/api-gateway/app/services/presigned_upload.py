import uuid

from app.config import settings
from app.services.media_tap import ensure_bucket, get_s3_client

VIDEO_UPLOAD_TTL_SECONDS = 3600


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
