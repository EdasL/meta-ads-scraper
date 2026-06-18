# tests/discovery/test_mine_review_keywords.py
import os
import pytest
from sqlalchemy import text
from agf.db.engine import get_engine
from agf.db.migrate import apply_migrations
from agf.discovery.liked_keyword_mining import Candidate
from scripts.mine_liked_keywords import mine
from scripts.review_mined_keywords import list_pending, promote

pytestmark = pytest.mark.skipif(
    not os.environ.get("AGF_TEST_DB_URL"), reason="needs AGF_TEST_DB_URL")

EMAIL = "mine@test.local"


def test_mine_inserts_pending_then_promote_to_trial():
    eng = get_engine(os.environ["AGF_TEST_DB_URL"])
    apply_migrations(eng)
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM discovery.dynamic_keywords WHERE source='liked-mining'"))
        adv = conn.execute(text(
            "SELECT advertiser_id FROM frontend.emerging_scaling_apps LIMIT 1")).scalar()
        if adv is None:
            pytest.skip("no matview rows")
        conn.execute(text("DELETE FROM frontend.app_ratings WHERE user_email=:e"), {"e": EMAIL})
        conn.execute(text(
            "INSERT INTO frontend.app_ratings (user_email,advertiser_id,rating,features) "
            "VALUES (:e,:a,'like','{}'::jsonb)"), {"e": EMAIL, "a": adv})

        def fake_propose(apps, **kw):
            return [Candidate("zzz ai stem separation", "like X", [adv])]

        n = mine(conn, EMAIL, propose_fn=fake_propose)
        assert n == 1
        pend = [p for p in list_pending(conn) if p["keyword"] == "zzz ai stem separation"]
        assert pend and pend[0]["status"] == "pending"
        # sweep selector must ignore pending
        swept = conn.execute(text(
            "SELECT count(*) FROM discovery.dynamic_keywords "
            "WHERE keyword='zzz ai stem separation' AND status IN ('trial','active')")).scalar()
        assert swept == 0
        promote(conn, "zzz ai stem separation")
        now_trial = conn.execute(text(
            "SELECT status FROM discovery.dynamic_keywords WHERE keyword='zzz ai stem separation'")).scalar()
        assert now_trial == "trial"
        conn.execute(text("DELETE FROM discovery.dynamic_keywords WHERE keyword='zzz ai stem separation'"))
        conn.execute(text("DELETE FROM frontend.app_ratings WHERE user_email=:e"), {"e": EMAIL})
