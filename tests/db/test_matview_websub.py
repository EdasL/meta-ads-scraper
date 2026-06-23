import os
import pytest
from sqlalchemy import text
from agf.db.engine import get_engine
from agf.db.migrate import apply_migrations

DB = os.environ.get("AGF_TEST_DB_URL")
pytestmark = pytest.mark.skipif(not DB, reason="needs AGF_TEST_DB_URL")


def test_matview_has_web_only_and_coalesces_identity():
    eng = get_engine(DB)
    apply_migrations(eng)
    with eng.connect() as c:
        # information_schema.columns excludes materialized views; use pg_attribute instead
        cols = {r[0] for r in c.execute(text(
            "SELECT attname FROM pg_attribute "
            "WHERE attrelid = 'frontend.emerging_scaling_apps'::regclass "
            "AND attnum > 0 AND NOT attisdropped"))}
    assert "web_only" in cols
    # identity columns still present (COALESCE keeps the same names)
    assert {"app_name", "icon_url", "tagline", "funnel_url"} <= cols
