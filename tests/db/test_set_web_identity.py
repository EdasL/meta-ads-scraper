# tests/db/test_set_web_identity.py
import os, pytest
from types import SimpleNamespace
from sqlalchemy import text
from agf.db.engine import get_engine
from agf.db.migrate import apply_migrations
from agf.db import repo

DB = os.environ.get("AGF_TEST_DB_URL")
pytestmark = pytest.mark.skipif(not DB, reason="needs AGF_TEST_DB_URL")

def test_set_web_identity_writes_columns():
    eng = get_engine(DB); apply_migrations(eng)
    with eng.begin() as c:
        aid = repo.upsert_advertiser(c, SimpleNamespace(
            page_id="WSID6", page_name="WS6", page_like_count=10))
        repo.set_web_identity(c, aid, payoff_kind="web_sub", name="Sleep Tight",
                              description="Better sleep.", icon_url="https://x/favicon.ico",
                              landing_url="https://trysleeptight.app/")
        row = c.execute(text(
            "SELECT web_payoff_kind, web_name, web_description, web_icon_url, web_landing_url "
            "FROM public.advertisers WHERE id=:a"), {"a": aid}).one()
    assert row.web_payoff_kind == "web_sub"
    assert row.web_name == "Sleep Tight"
    assert row.web_landing_url == "https://trysleeptight.app/"


def test_set_web_identity_coalesce_protects_existing_values():
    """Second call with name=None/description=None must not blank previously written values."""
    eng = get_engine(DB); apply_migrations(eng)
    with eng.begin() as c:
        aid = repo.upsert_advertiser(c, SimpleNamespace(
            page_id="WSID7", page_name="WS7", page_like_count=5))
        # First write: real values
        repo.set_web_identity(c, aid, payoff_kind="web_sub", name="Real Name",
                              description="Real desc.", icon_url="https://x/favicon.ico",
                              landing_url="https://example.com/")
        # Second write: name and description are None
        repo.set_web_identity(c, aid, payoff_kind="web_sub", name=None,
                              description=None,
                              icon_url=None, landing_url="https://example.com/")
        row = c.execute(text(
            "SELECT web_name, web_description FROM public.advertisers WHERE id=:a"),
            {"a": aid}).one()
    # COALESCE must protect previously-written values from being overwritten with NULL
    assert row.web_name == "Real Name"
    assert row.web_description == "Real desc."
