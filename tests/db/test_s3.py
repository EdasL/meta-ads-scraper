import os
import pytest


@pytest.mark.integration
def test_s3_put_get_roundtrip():
    from agf.storage.s3 import get_s3, put_bytes
    client = get_s3()
    key = "test/hello.txt"
    put_bytes(key, b"hello-minio", "text/plain", client=client)
    body = client.get_object(Bucket=os.environ["S3_BUCKET"], Key=key)["Body"].read()
    assert body == b"hello-minio"
