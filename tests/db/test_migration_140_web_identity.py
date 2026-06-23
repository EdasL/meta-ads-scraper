# tests/db/test_migration_140_web_identity.py
import os
import pytest
from sqlalchemy import text
from agf.db.engine import get_engine
from agf.db.migrate import apply_migrations

DB = os.environ.get("AGF_TEST_DB_URL")
pytestmark = pytest.mark.skipif(not DB, reason="needs AGF_TEST_DB_URL")

def test_web_identity_columns_exist():
    eng = get_engine(DB)
    apply_migrations(eng)
    with eng.connect() as c:
        cols = {r[0] for r in c.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='advertisers'"))}
    assert {"web_payoff_kind", "web_name", "web_description",
            "web_icon_url", "web_landing_url"} <= cols
