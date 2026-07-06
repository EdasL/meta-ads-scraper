# tests/discovery/test_candidate_embed.py
import os
import pytest
from sqlalchemy import text
from agf.db.engine import get_engine
from agf.db.migrate import apply_migrations
from agf.enrich import AppMeta
from agf.discovery.candidate_embed import embed_candidates

pytestmark = pytest.mark.skipif(
    not os.environ.get("AGF_TEST_DB_URL"), reason="needs AGF_TEST_DB_URL")


def _meta(tid, name, desc):
    return AppMeta(track_id=tid, name=name, seller="", genre="Music",
                   release_date="", rating_count=0, avg_rating=0.0, description=desc)


def test_embed_candidates_fetches_embeds_once_and_cascades():
    eng = get_engine(os.environ["AGF_TEST_DB_URL"])
    apply_migrations(eng)
    fake_itunes = lambda client, ids, **kw: {"7777": _meta("7777", "VocalAI", "AI vocal remover")}
    fake_embed = lambda texts, **kw: [[1.0] + [0.0] * 767 for _ in texts]
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM discovery.candidates WHERE page_id='TPC1'"))
        cid = conn.execute(text(
            "INSERT INTO discovery.candidates (page_id,page_name,store,store_id,"
            "source_keyword,technique,status) VALUES "
            "('TPC1','VocalAI','ios','7777','vocal','niche','kept') RETURNING id")).scalar()
        first = embed_candidates(conn, itunes_client=None,
                                 lookup_itunes=fake_itunes, embed_fn=fake_embed)
        assert first["embedded"] == 1
        row = conn.execute(text(
            "SELECT dim FROM discovery.candidate_embeddings WHERE candidate_id=:i"),
            {"i": cid}).first()
        assert row is not None and row.dim == 768
        second = embed_candidates(conn, itunes_client=None,           # already embedded
                                  lookup_itunes=fake_itunes, embed_fn=fake_embed)
        assert second["embedded"] == 0
        conn.execute(text("DELETE FROM discovery.candidates WHERE id=:i"), {"i": cid})
        gone = conn.execute(text(
            "SELECT count(*) FROM discovery.candidate_embeddings WHERE candidate_id=:i"),
            {"i": cid}).scalar()
        assert gone == 0


def test_wholesale_empty_lookup_skips_instead_of_sentinel():
    """A lookup that returns NOTHING for a non-empty request is a failure (timeout /
    API down), not proof of delisting — the 2026-06-29..30 incident wrote 910 permanent
    'unfetchable' sentinels for Android apps that were fetchable. Those rows must be
    SKIPPED (no row written) so the next run retries them."""
    eng = get_engine(os.environ["AGF_TEST_DB_URL"])
    apply_migrations(eng)
    empty_itunes = lambda client, ids, **kw: {}      # wholesale failure
    fake_embed = lambda texts, **kw: [[1.0] + [0.0] * 767 for _ in texts]
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM discovery.candidates WHERE page_id='TPC2'"))
        cid = conn.execute(text(
            "INSERT INTO discovery.candidates (page_id,page_name,store,store_id,"
            "source_keyword,technique,status) VALUES "
            "('TPC2','Gone','ios','404','k','niche','kept') RETURNING id")).scalar()
        out = embed_candidates(conn, itunes_client=None,
                               lookup_itunes=empty_itunes, embed_fn=fake_embed)
        assert out["embedded"] == 0
        assert out["sentinel"] == 0                  # NO permanent poison
        assert out["skipped"] == 1                   # retried next run
        row = conn.execute(text(
            "SELECT count(*) FROM discovery.candidate_embeddings WHERE candidate_id=:i"),
            {"i": cid}).scalar()
        assert row == 0                              # nothing written
        # Next call still sees it pending (retry)
        out2 = embed_candidates(conn, itunes_client=None,
                                lookup_itunes=empty_itunes, embed_fn=fake_embed)
        assert out2["pending"] == 1
        conn.execute(text("DELETE FROM discovery.candidates WHERE id=:i"), {"i": cid})


def test_missing_id_in_successful_lookup_writes_sentinel():
    """An id absent from an otherwise-successful lookup IS delisted — sentinel it so
    it isn't re-fetched forever."""
    eng = get_engine(os.environ["AGF_TEST_DB_URL"])
    apply_migrations(eng)
    partial_itunes = lambda client, ids, **kw: {"8888": _meta("8888", "Alive", "still here")}
    fake_embed = lambda texts, **kw: [[1.0] + [0.0] * 767 for _ in texts]
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM discovery.candidates WHERE page_id IN ('TPC4','TPC5')"))
        alive = conn.execute(text(
            "INSERT INTO discovery.candidates (page_id,page_name,store,store_id,"
            "source_keyword,technique,status) VALUES "
            "('TPC4','Alive','ios','8888','k','niche','kept') RETURNING id")).scalar()
        gone = conn.execute(text(
            "INSERT INTO discovery.candidates (page_id,page_name,store,store_id,"
            "source_keyword,technique,status) VALUES "
            "('TPC5','Gone','ios','404404','k','niche','kept') RETURNING id")).scalar()
        out = embed_candidates(conn, itunes_client=None,
                               lookup_itunes=partial_itunes, embed_fn=fake_embed)
        assert out["embedded"] == 1                  # the found one embeds
        assert out["sentinel"] == 1                  # the missing one is delisted
        model = conn.execute(text(
            "SELECT model FROM discovery.candidate_embeddings WHERE candidate_id=:i"),
            {"i": gone}).scalar()
        assert model == "unfetchable"
        conn.execute(text(
            "DELETE FROM discovery.candidates WHERE id IN (:a,:b)"),
            {"a": alive, "b": gone})


def test_apps_table_is_the_first_listing_source():
    """A candidate whose (store, store_id) already has a resolved public.apps row embeds
    from that row — the store lookup must not even be called for it. This is what makes
    a transient bulk-lookup failure unable to poison already-resolved candidates."""
    eng = get_engine(os.environ["AGF_TEST_DB_URL"])
    apply_migrations(eng)

    def boom_lookup(client, ids, **kw):
        raise AssertionError(f"store lookup called for {ids}")

    fake_embed = lambda texts, **kw: [[1.0] + [0.0] * 767 for _ in texts]
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM discovery.candidates WHERE page_id='TPC6'"))
        conn.execute(text("DELETE FROM public.apps WHERE store_id='com.tpc6.resolved'"))
        conn.execute(text(
            "INSERT INTO public.apps (store, store_id, name, genre, description) VALUES "
            "('android','com.tpc6.resolved','ResolvedApp','Health & Fitness',"
            "'a sleep tracking app already resolved by the metadata stage')"))
        cid = conn.execute(text(
            "INSERT INTO discovery.candidates (page_id,page_name,store,store_id,"
            "source_keyword,technique,status) VALUES "
            "('TPC6','ResolvedApp','android','com.tpc6.resolved','k','niche','kept') "
            "RETURNING id")).scalar()
        out = embed_candidates(conn, itunes_client=None,
                               lookup_itunes=boom_lookup, lookup_play=boom_lookup,
                               embed_fn=fake_embed)
        assert out["embedded"] == 1
        dim = conn.execute(text(
            "SELECT dim FROM discovery.candidate_embeddings WHERE candidate_id=:i"),
            {"i": cid}).scalar()
        assert dim == 768
        conn.execute(text("DELETE FROM discovery.candidates WHERE id=:i"), {"i": cid})
        conn.execute(text("DELETE FROM public.apps WHERE store_id='com.tpc6.resolved'"))


def test_empty_text_candidate_writes_sentinel():
    """Candidates that produce empty embed text get a sentinel row, not a skip."""
    eng = get_engine(os.environ["AGF_TEST_DB_URL"])
    apply_migrations(eng)
    # App exists in lookup but yields empty text (no name, no genre, no description)
    blank_meta = AppMeta(track_id="9999", name="", seller="", genre="",
                         release_date="", rating_count=0, avg_rating=0.0, description="")
    blank_itunes = lambda client, ids, **kw: {"9999": blank_meta}
    fake_embed = lambda texts, **kw: [[1.0] + [0.0] * 767 for _ in texts]
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM discovery.candidates WHERE page_id='TPC3'"))
        cid = conn.execute(text(
            "INSERT INTO discovery.candidates (page_id,page_name,store,store_id,"
            "source_keyword,technique,status) VALUES "
            "('TPC3','Blank','ios','9999','k','niche','kept') RETURNING id")).scalar()
        out = embed_candidates(conn, itunes_client=None,
                               lookup_itunes=blank_itunes, embed_fn=fake_embed)
        assert out["embedded"] == 0
        assert out["sentinel"] == 1
        row = conn.execute(text(
            "SELECT dim, model FROM discovery.candidate_embeddings WHERE candidate_id=:i"),
            {"i": cid}).first()
        assert row is not None and row.dim == 0 and row.model == "unfetchable"
        conn.execute(text("DELETE FROM discovery.candidates WHERE id=:i"), {"i": cid})
