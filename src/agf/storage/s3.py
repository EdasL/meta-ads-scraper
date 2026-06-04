"""S3/MinIO client (master-plan §3.8). Env: S3_ENDPOINT, MINIO_ACCESS_KEY,
MINIO_SECRET_KEY, S3_BUCKET. Plan 6 passes this client into download/capture jobs."""
import os
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

_BUCKET = lambda: os.environ.get("S3_BUCKET", "agf")


def get_s3():
    """Return a boto3 S3 client pointed at MinIO/S3, ensuring the bucket exists."""
    client = boto3.client(
        "s3",
        endpoint_url=os.environ.get("S3_ENDPOINT"),
        aws_access_key_id=os.environ.get("MINIO_ACCESS_KEY"),
        aws_secret_access_key=os.environ.get("MINIO_SECRET_KEY"),
        region_name=os.environ.get("S3_REGION", "us-east-1"),
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    bucket = _BUCKET()
    try:
        client.head_bucket(Bucket=bucket)
    except ClientError:
        client.create_bucket(Bucket=bucket)
    return client


def put_bytes(key: str, data: bytes, content_type: str, client=None) -> str:
    """Upload bytes under `key`; return the key. Creates the client if not given."""
    client = client or get_s3()
    client.put_object(Bucket=_BUCKET(), Key=key, Body=data, ContentType=content_type)
    return key
