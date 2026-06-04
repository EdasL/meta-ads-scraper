import pytest
from sqlalchemy import text
from fastapi.testclient import TestClient
from agf.api.app import create_app
from agf.api import deps


def _writes(client):
    paths = client.get("/openapi.json").json()["paths"]
    return {(p, m.upper()) for p, ms in paths.items() for m in ms
            if m.upper() in {"POST", "PUT", "PATCH", "DELETE"}}


def test_healthz_ok(_migrated_engine):
    client = TestClient(create_app(_migrated_engine))
    assert client.get("/healthz").json()["status"] == "ok"


def test_openapi_only_write_is_review_decision(_migrated_engine):
    # the ONLY mutation in the whole API is recording a human review verdict
    client = TestClient(create_app(_migrated_engine))
    assert _writes(client) == {("/api/review/{advertiser_id}/decision", "POST")}


def test_read_only_conn_denies_writes(_migrated_engine):
    app = create_app(_migrated_engine)

    class _Req:
        pass
    req = _Req()
    req.app = app
    it = deps.get_conn(req)
    conn = next(it)
    try:
        with pytest.raises(Exception):
            conn.execute(text("CREATE TEMP TABLE _ro_probe(i int)"))   # write -> RO txn rejects
    finally:
        it.close()
