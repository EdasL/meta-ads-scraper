# tests/scoring/test_score_content_preferences.py
import os
import pytest
from sqlalchemy import text
from agf.db.engine import get_engine
from agf.db.migrate import apply_migrations
from scripts.score_content_preferences import score_all_users

pytestmark = pytest.mark.skipif(
    not os.environ.get("AGF_TEST_DB_URL"), reason="needs AGF_TEST_DB_URL")

EMAIL = "ct_pref@test.local"

def test_scores_written_for_user():
    eng = get_engine(os.environ["AGF_TEST_DB_URL"])
    apply_migrations(eng)
    # Synthetic advertiser_ids — independent of matview row count. The scorer reads
    # embeddings + ratings BY advertiser_id; the matview only supplies names /
    # cold-start gem_score, so labeled apps need not be matview members. This keeps
    # the test deterministic regardless of how many rows the Go IT seed created.
    adv = list(range(900001, 900015))            # 14 ids: 10 like + 4 dislike
    e1 = [1.0] + [0.0] * 767                      # liked cluster
    e2 = [0.0, 1.0] + [0.0] * 766                 # disliked cluster
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM frontend.app_ratings WHERE user_email=:e"), {"e": EMAIL})
        conn.execute(text("DELETE FROM frontend.preference_scores WHERE user_email=:e"), {"e": EMAIL})
        conn.execute(text("DELETE FROM frontend.app_embeddings WHERE advertiser_id = ANY(:ids)"), {"ids": adv})
        for i, a in enumerate(adv):
            vec = e1 if i < 10 else e2
            conn.execute(text(
                "INSERT INTO frontend.app_embeddings (advertiser_id,embedding,content_hash,dim,model) "
                "VALUES (:a,:v,:h,768,'t') ON CONFLICT (advertiser_id) DO UPDATE SET embedding=EXCLUDED.embedding"),
                {"a": a, "v": vec, "h": f"h{i}"})
            rating = "like" if i < 10 else "dislike"   # 10 likes -> above MIN_LIKES (8)
            conn.execute(text(
                "INSERT INTO frontend.app_ratings (user_email,advertiser_id,rating,features) "
                "VALUES (:e,:a,:r,'{}'::jsonb)"), {"e": EMAIL, "a": a, "r": rating})
        counts = score_all_users(conn)
        assert counts.get(EMAIL, 0) >= 12          # scorer ranks every embedding
        versions = [r[0] for r in conn.execute(text(
            "SELECT DISTINCT model_version FROM frontend.preference_scores "
            "WHERE user_email=:e"), {"e": EMAIL})]
        assert versions == ["content-sim-v1"]      # 10 likes -> active model, not coldstart
        conn.execute(text("DELETE FROM frontend.app_ratings WHERE user_email=:e"), {"e": EMAIL})
        conn.execute(text("DELETE FROM frontend.preference_scores WHERE user_email=:e"), {"e": EMAIL})
        conn.execute(text("DELETE FROM frontend.app_embeddings WHERE advertiser_id = ANY(:ids)"), {"ids": adv})
