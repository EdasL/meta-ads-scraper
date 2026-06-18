import os
import pytest
from sqlalchemy import text
from agf.db.engine import get_engine
from agf.db.migrate import apply_migrations
from scripts.embed_apps import embed_apps

pytestmark = pytest.mark.skipif(
    not os.environ.get("AGF_TEST_DB_URL"), reason="needs AGF_TEST_DB_URL")


def test_embed_apps_is_incremental(monkeypatch):
    eng = get_engine(os.environ["AGF_TEST_DB_URL"])
    apply_migrations(eng)
    calls = {"n": 0}
    def fake_embed(texts, **kw):
        calls["n"] += len(texts)
        return [[1.0] + [0.0] * 767 for _ in texts]
    with eng.begin() as conn:
        # Hermetic: a prior committed run leaves app_embeddings populated, so the first
        # embed_apps below would find 0 new and the assert would drift. Clear it first.
        conn.execute(text("DELETE FROM frontend.app_embeddings"))
        # Use a real advertiser already present in the matview (test DB is seeded by Go IT).
        row = conn.execute(text(
            "SELECT advertiser_id FROM frontend.emerging_scaling_apps LIMIT 1")).first()
        if row is None:
            pytest.skip("no matview rows in test DB")
        first = embed_apps(conn, embed_fn=fake_embed)
        assert first["embedded"] >= 1
        n_after_first = calls["n"]
        second = embed_apps(conn, embed_fn=fake_embed)   # nothing changed
        assert second["embedded"] == 0
        assert calls["n"] == n_after_first               # no re-embed
