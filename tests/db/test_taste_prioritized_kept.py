# tests/db/test_taste_prioritized_kept.py
import math
import os
import pytest
from sqlalchemy import text
from agf.db.engine import get_engine
from agf.db.migrate import apply_migrations
from agf.db.candidates import taste_prioritized_kept, kept_advertiser_ids

pytestmark = pytest.mark.skipif(
    not os.environ.get("AGF_TEST_DB_URL"), reason="needs AGF_TEST_DB_URL")

def test_falls_back_to_fifo_when_user_has_no_likes():
    eng = get_engine(os.environ["AGF_TEST_DB_URL"])
    apply_migrations(eng)
    with eng.begin() as conn:
        fifo = kept_advertiser_ids(conn)
        out = taste_prioritized_kept(conn, user="nobody_tp@test.local", explore_frac=0.25)
        assert out == fifo                       # cold-start user -> identical FIFO


def _insert_advertiser(conn, page_id):
    """Insert a minimal advertiser row and return its id (SERIAL)."""
    return conn.execute(text(
        "INSERT INTO public.advertisers (page_id) VALUES (:p) "
        "ON CONFLICT (page_id) DO UPDATE SET page_id=EXCLUDED.page_id RETURNING id"),
        {"p": page_id}).scalar()


def test_taste_prioritized_kept_ranks_liked_cluster_first():
    """Near-liked candidate ranks ahead of near-disliked one; same set as FIFO."""
    eng = get_engine(os.environ["AGF_TEST_DB_URL"])
    apply_migrations(eng)

    USER = "tp_test_hermetic@test.local"
    DIM = 768
    liked_vec = [1.0] + [0.0] * (DIM - 1)
    disliked_vec = [0.0, 1.0] + [0.0] * (DIM - 2)

    # Unique page ids for this test to avoid collisions
    PAGE_LIKED = "TP_HERM_LIKED_CAND"
    PAGE_DISLIKED = "TP_HERM_DISLIKED_CAND"
    LIKE_PAGES = [f"TP_HERM_LIKE_RATER_{i}" for i in range(8)]
    DISLIKE_PAGES = [f"TP_HERM_DISLIKE_RATER_{i}" for i in range(3)]
    ALL_PAGES = [PAGE_LIKED, PAGE_DISLIKED] + LIKE_PAGES + DISLIKE_PAGES

    with eng.begin() as conn:
        # ---- cleanup any previous run ----
        conn.execute(text("DELETE FROM discovery.candidates WHERE page_id = ANY(:ps)"),
                     {"ps": [PAGE_LIKED, PAGE_DISLIKED]})
        conn.execute(text("DELETE FROM frontend.app_ratings WHERE user_email=:u"), {"u": USER})
        # clean up advertiser rows that may linger from previous run
        conn.execute(text("DELETE FROM public.advertisers WHERE page_id = ANY(:ps)"),
                     {"ps": ALL_PAGES})

        # ---- seed: 8 like-rated advertisers with liked-cluster embeddings ----
        like_adv_ids = []
        for pg in LIKE_PAGES:
            adv = _insert_advertiser(conn, pg)
            like_adv_ids.append(adv)
            conn.execute(text(
                "INSERT INTO frontend.app_embeddings (advertiser_id, embedding, content_hash, dim, model) "
                "VALUES (:a, :e, :h, :d, :m) "
                "ON CONFLICT (advertiser_id) DO UPDATE SET embedding=EXCLUDED.embedding"),
                {"a": adv, "e": liked_vec, "h": pg, "d": DIM, "m": "test"})
            conn.execute(text(
                "INSERT INTO frontend.app_ratings (user_email, advertiser_id, rating) "
                "VALUES (:u, :a, 'like') "
                "ON CONFLICT (user_email, advertiser_id) DO UPDATE SET rating='like'"),
                {"u": USER, "a": adv})

        # ---- seed: 3 dislike-rated advertisers with disliked-cluster embeddings ----
        dislike_adv_ids = []
        for pg in DISLIKE_PAGES:
            adv = _insert_advertiser(conn, pg)
            dislike_adv_ids.append(adv)
            conn.execute(text(
                "INSERT INTO frontend.app_embeddings (advertiser_id, embedding, content_hash, dim, model) "
                "VALUES (:a, :e, :h, :d, :m) "
                "ON CONFLICT (advertiser_id) DO UPDATE SET embedding=EXCLUDED.embedding"),
                {"a": adv, "e": disliked_vec, "h": pg, "d": DIM, "m": "test"})
            conn.execute(text(
                "INSERT INTO frontend.app_ratings (user_email, advertiser_id, rating) "
                "VALUES (:u, :a, 'dislike') "
                "ON CONFLICT (user_email, advertiser_id) DO UPDATE SET rating='dislike'"),
                {"u": USER, "a": adv})

        # ---- seed: two kept candidates ----
        adv_cand_liked = _insert_advertiser(conn, PAGE_LIKED)
        adv_cand_disliked = _insert_advertiser(conn, PAGE_DISLIKED)

        cid_liked = conn.execute(text(
            "INSERT INTO discovery.candidates "
            "(page_id,page_name,store,store_id,source_keyword,technique,status,kept_advertiser_id) "
            "VALUES (:p,'LikedApp','ios','55551','k','niche','kept',:a) RETURNING id"),
            {"p": PAGE_LIKED, "a": adv_cand_liked}).scalar()
        cid_disliked = conn.execute(text(
            "INSERT INTO discovery.candidates "
            "(page_id,page_name,store,store_id,source_keyword,technique,status,kept_advertiser_id) "
            "VALUES (:p,'DislikedApp','ios','55552','k','niche','kept',:a) RETURNING id"),
            {"p": PAGE_DISLIKED, "a": adv_cand_disliked}).scalar()

        # candidate_embeddings: liked near liked cluster, disliked near disliked cluster
        conn.execute(text(
            "INSERT INTO discovery.candidate_embeddings "
            "(candidate_id, embedding, content_hash, dim, model) "
            "VALUES (:c, :e, :h, :d, :m) "
            "ON CONFLICT (candidate_id) DO UPDATE SET embedding=EXCLUDED.embedding"),
            {"c": cid_liked, "e": liked_vec, "h": "cand_l", "d": DIM, "m": "test"})
        conn.execute(text(
            "INSERT INTO discovery.candidate_embeddings "
            "(candidate_id, embedding, content_hash, dim, model) "
            "VALUES (:c, :e, :h, :d, :m) "
            "ON CONFLICT (candidate_id) DO UPDATE SET embedding=EXCLUDED.embedding"),
            {"c": cid_disliked, "e": disliked_vec, "h": "cand_d", "d": DIM, "m": "test"})

        # ---- run ----
        out = taste_prioritized_kept(conn, user=USER, explore_frac=0.0)

        # Same set as FIFO (nothing dropped)
        fifo = kept_advertiser_ids(conn)
        assert set(out) == set(fifo), f"output set differs: {set(out)} vs {set(fifo)}"

        # Near-liked candidate must appear before near-disliked one
        our_adv = {adv_cand_liked, adv_cand_disliked}
        our_ranked = [a for a in out if a in our_adv]
        assert our_ranked[0] == adv_cand_liked, (
            f"expected liked adv first, got {our_ranked}")

        # ---- cleanup ----
        conn.execute(text("DELETE FROM discovery.candidates WHERE page_id = ANY(:ps)"),
                     {"ps": [PAGE_LIKED, PAGE_DISLIKED]})
        conn.execute(text("DELETE FROM frontend.app_ratings WHERE user_email=:u"), {"u": USER})
        conn.execute(text(
            "DELETE FROM frontend.app_embeddings WHERE advertiser_id = ANY(:ids)"),
            {"ids": like_adv_ids + dislike_adv_ids + [adv_cand_liked, adv_cand_disliked]})
        conn.execute(text("DELETE FROM public.advertisers WHERE page_id = ANY(:ps)"),
                     {"ps": ALL_PAGES})
