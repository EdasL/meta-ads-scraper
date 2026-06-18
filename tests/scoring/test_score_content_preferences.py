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

def test_rated_excluded_unrated_ranked():
    eng = get_engine(os.environ["AGF_TEST_DB_URL"])
    apply_migrations(eng)
    # Synthetic advertiser_ids — independent of matview row count. The scorer reads
    # embeddings + ratings BY advertiser_id; the matview only supplies names /
    # cold-start gem_score, so labeled apps need not be matview members.
    rated = list(range(900001, 900015))           # 10 like + 4 dislike — for FITTING only
    unrated = list(range(900101, 900107))         # 6 unrated — the apps that get ranked
    all_ids = rated + unrated
    e_like = [1.0] + [0.0] * 767                   # liked cluster
    e_dislike = [0.0, 1.0] + [0.0] * 766           # disliked cluster
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM frontend.app_ratings WHERE user_email=:e"), {"e": EMAIL})
        conn.execute(text("DELETE FROM frontend.preference_scores WHERE user_email=:e"), {"e": EMAIL})
        conn.execute(text("DELETE FROM frontend.app_embeddings WHERE advertiser_id = ANY(:ids)"), {"ids": all_ids})

        def _emb(a, vec, h):
            conn.execute(text(
                "INSERT INTO frontend.app_embeddings (advertiser_id,embedding,content_hash,dim,model) "
                "VALUES (:a,:v,:h,768,'t') ON CONFLICT (advertiser_id) DO UPDATE SET embedding=EXCLUDED.embedding"),
                {"a": a, "v": vec, "h": h})

        for i, a in enumerate(rated):              # rated apps: embed + rate
            _emb(a, e_like if i < 10 else e_dislike, f"r{i}")
            conn.execute(text(
                "INSERT INTO frontend.app_ratings (user_email,advertiser_id,rating,features) "
                "VALUES (:e,:a,:r,'{}'::jsonb)"),
                {"e": EMAIL, "a": a, "r": "like" if i < 10 else "dislike"})
        for i, a in enumerate(unrated):            # unrated apps: embed only, NO rating
            _emb(a, e_like if i < 3 else e_dislike, f"u{i}")

        score_all_users(conn)

        scored_ids = {r[0] for r in conn.execute(text(
            "SELECT advertiser_id FROM frontend.preference_scores WHERE user_email=:e"), {"e": EMAIL})}
        assert scored_ids.isdisjoint(set(rated))   # already-rated apps are NOT ranked
        assert set(unrated) <= scored_ids          # every unrated app IS ranked
        versions = [r[0] for r in conn.execute(text(
            "SELECT DISTINCT model_version FROM frontend.preference_scores "
            "WHERE user_email=:e"), {"e": EMAIL})]
        assert versions == ["content-sim-v1"]      # 10 likes -> active model, not coldstart
        rank = {a: q for a, q in conn.execute(text(
            "SELECT advertiser_id, queue_rank FROM frontend.preference_scores "
            "WHERE user_email=:e"), {"e": EMAIL})}
        assert rank[unrated[0]] < rank[unrated[5]]  # like-cluster app outranks dislike-cluster app

        conn.execute(text("DELETE FROM frontend.app_ratings WHERE user_email=:e"), {"e": EMAIL})
        conn.execute(text("DELETE FROM frontend.preference_scores WHERE user_email=:e"), {"e": EMAIL})
        conn.execute(text("DELETE FROM frontend.app_embeddings WHERE advertiser_id = ANY(:ids)"), {"ids": all_ids})
