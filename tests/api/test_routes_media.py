"""Media proxy route (GET /api/media/{key}). Integration: needs MinIO/S3 (the
S3_* env). Puts a real object, fetches it through the running app, and asserts the
prefix whitelist rejects everything else."""
import os
import pytest
from fastapi.testclient import TestClient
from agf.api.app import create_app


@pytest.fixture
def s3_cleanup():
    if not os.environ.get("S3_ENDPOINT"):
        pytest.skip("media route test needs MinIO/S3 (set S3_ENDPOINT)")
    from agf.storage.s3 import get_s3, _BUCKET
    keys: list[str] = []
    yield keys
    s3 = get_s3()
    for k in keys:
        try:
            s3.delete_object(Bucket=_BUCKET(), Key=k)
        except Exception:
            pass


def test_media_serves_bytes_and_whitelists_prefix(_migrated_engine, s3_cleanup):
    from agf.storage.s3 import put_bytes

    key = "creatives/p7media_smoke.png"
    png = b"\x89PNG\r\n\x1a\n" + bytes(range(64))   # arbitrary non-empty payload
    put_bytes(key, png, "image/png")
    s3_cleanup.append(key)

    c = TestClient(create_app(_migrated_engine))

    r = c.get(f"/api/media/{key}")
    assert r.status_code == 200
    assert r.content == png
    assert r.headers["content-type"].startswith("image/png")

    # off-whitelist prefixes are rejected (the route's whole authz model)
    assert c.get("/api/media/secrets/anything").status_code == 403
    assert c.get("/api/media/etc/passwd").status_code == 403

    # known prefix but missing object → 404, not a 500
    assert c.get("/api/media/creatives/does_not_exist.png").status_code == 404
