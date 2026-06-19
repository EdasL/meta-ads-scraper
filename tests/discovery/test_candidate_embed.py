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


def test_unfetchable_candidate_writes_sentinel():
    """Unfetchable candidates get a sentinel row (dim=0) so they aren't re-fetched."""
    eng = get_engine(os.environ["AGF_TEST_DB_URL"])
    apply_migrations(eng)
    empty_itunes = lambda client, ids, **kw: {}      # listing not found
    fake_embed = lambda texts, **kw: [[1.0] + [0.0] * 767 for _ in texts]
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM discovery.candidates WHERE page_id='TPC2'"))
        cid = conn.execute(text(
            "INSERT INTO discovery.candidates (page_id,page_name,store,store_id,"
            "source_keyword,technique,status) VALUES "
            "('TPC2','Gone','ios','404','k','niche','kept') RETURNING id")).scalar()
        out = embed_candidates(conn, itunes_client=None,
                               lookup_itunes=empty_itunes, embed_fn=fake_embed)
        assert out["embedded"] == 0                  # not counted as embedded
        assert out["sentinel"] == 1                  # but a sentinel was written
        row = conn.execute(text(
            "SELECT dim, model FROM discovery.candidate_embeddings WHERE candidate_id=:i"),
            {"i": cid}).first()
        assert row is not None and row.dim == 0 and row.model == "unfetchable"
        # Second call: sentinel row exists, so NOT re-fetched
        out2 = embed_candidates(conn, itunes_client=None,
                                lookup_itunes=empty_itunes, embed_fn=fake_embed)
        assert out2["pending"] == 0                  # excluded by NOT EXISTS
        conn.execute(text("DELETE FROM discovery.candidates WHERE id=:i"), {"i": cid})


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
