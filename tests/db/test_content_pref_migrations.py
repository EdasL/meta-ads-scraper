import os
import pytest
from sqlalchemy import text
from agf.db.engine import get_engine
from agf.db.migrate import apply_migrations

pytestmark = pytest.mark.skipif(
    not os.environ.get("AGF_TEST_DB_URL"), reason="needs AGF_TEST_DB_URL")


def test_migrations_apply_and_shape():
    eng = get_engine(os.environ["AGF_TEST_DB_URL"])
    apply_migrations(eng)
    with eng.begin() as c:
        cols = {r[0] for r in c.execute(text(
            "SELECT attname FROM pg_attribute "
            "WHERE attrelid='frontend.app_embeddings'::regclass "
            "AND attnum>0 AND NOT attisdropped"))}
        assert {"advertiser_id", "embedding", "content_hash", "dim",
                "model", "embedded_at"} <= cols
        dk = {r[0] for r in c.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='discovery' AND table_name='dynamic_keywords'"))}
        assert {"rationale", "inspired_by"} <= dk
        # 'pending' must now be accepted by the status check
        c.execute(text(
            "INSERT INTO discovery.dynamic_keywords (keyword, source, status, kind) "
            "VALUES ('zzz_test_pending', 'liked-mining', 'pending', 'app')"))
        c.execute(text(
            "DELETE FROM discovery.dynamic_keywords WHERE keyword='zzz_test_pending'"))
