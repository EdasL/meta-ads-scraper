# tests/scoring/test_features_web_payoff.py
import os, datetime as dt
import pytest
from sqlalchemy import text
from agf.db.engine import get_engine
from agf.db.migrate import apply_migrations
from agf.scoring.features import build_features

DB = os.environ.get("AGF_TEST_DB_URL")
pytestmark = pytest.mark.skipif(not DB, reason="needs AGF_TEST_DB_URL")

def test_build_features_surfaces_web_payoff_kind():
    eng = get_engine(DB); apply_migrations(eng)
    with eng.begin() as c:
        aid = c.execute(text(
            "INSERT INTO public.advertisers (page_id, page_name, web_payoff_kind) "
            "VALUES ('WSTEST1', 'WS Test', 'web_sub') "
            "ON CONFLICT (page_id) DO UPDATE SET web_payoff_kind='web_sub' "
            "RETURNING id")).scalar_one()
    with eng.connect() as c:
        fv = build_features(c, aid, dt.date(2026, 6, 23))
    assert fv.web_payoff_kind == "web_sub"

def test_build_features_web_payoff_none_for_normal_advertiser():
    eng = get_engine(DB); apply_migrations(eng)
    with eng.begin() as c:
        aid = c.execute(text(
            "INSERT INTO public.advertisers (page_id, page_name) "
            "VALUES ('WSTEST2', 'Normal') ON CONFLICT (page_id) DO UPDATE "
            "SET page_name='Normal' RETURNING id")).scalar_one()
    with eng.connect() as c:
        fv = build_features(c, aid, dt.date(2026, 6, 23))
    assert fv.web_payoff_kind is None
