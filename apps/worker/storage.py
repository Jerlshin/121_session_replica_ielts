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


def ensure_bucket(client=None) -> None:
    client = client or get_s3_client()
    try:
        client.head_bucket(Bucket=settings.s3_bucket)
    except ClientError:
        client.create_bucket(Bucket=settings.s3_bucket)
