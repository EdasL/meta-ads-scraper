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


def test_openapi_has_no_writes_yet(_migrated_engine):
    # Task 4: routers are stubs -> zero write endpoints. Task 6 tightens this to the
    # single review POST.
    client = TestClient(create_app(_migrated_engine))
    assert _writes(client) == set()


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
