"""S3 client factory — mirrors api-gateway/app/services/media_tap.py's
get_s3_client/ensure_bucket, worker-scoped. Each app owns its own storage
access per the monorepo's independent-deployables boundary; nothing here
is shared code today.
"""
import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import ClientError

from config import settings


def get_s3_client():
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
        config=BotoConfig(signature_version="s3v4"),
    )


def configure_bucket_lifecycle(client=None) -> None:
    """Encodes Spec 01 §7's raw-video/ retention window as actual bucket
    infrastructure (Spec 04 §2 Phase 8 security/compliance audit) instead
    of only a table in a doc. `PutBucketLifecycleConfiguration` replaces
    any existing configuration wholesale, so this is safe to call again if
    the retention policy ever changes."""
    client = client or get_s3_client()
    client.put_bucket_lifecycle_configuration(
        Bucket=settings.s3_bucket,
        LifecycleConfiguration={
            "Rules": [
                {
                    "ID": "raw-video-retention",
                    "Filter": {"Prefix": "raw-video/"},
                    "Status": "Enabled",
                    "Expiration": {"Days": settings.raw_video_retention_days},
                }
            ]
        },
    )


def ensure_bucket(client=None) -> None:
    client = client or get_s3_client()
    try:
        client.head_bucket(Bucket=settings.s3_bucket)
    except ClientError:
        client.create_bucket(Bucket=settings.s3_bucket)
        configure_bucket_lifecycle(client)
