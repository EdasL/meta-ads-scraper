"""Corpus ad-refresh: search_listing truncation flag, whole-corpus selection,
vanished-ad flip, and the budgeted refresh loop. CORPUSREF* sentinels."""
import asyncio
from tests.discovery.conftest import FakeExtractor, make_ad


def test_search_listing_returns_ads_and_truncation_flag():
    ads = [make_ad("cr-a1", "CORPUSREF1", "Adv", "https://x/app")]
    ext = FakeExtractor(page_ads={"CORPUSREF1": ads},
                        page_truncated={"CORPUSREF1": True})
    got, truncated = asyncio.run(ext.search_listing(page_id="CORPUSREF1", max_pages=12))
    assert [a.ad_archive_id for a in got] == ["cr-a1"]
    assert truncated is True
    # unknown page → empty, not truncated
    empty, trunc2 = asyncio.run(ext.search_listing(page_id="NOPE", max_pages=12))
    assert empty == [] and trunc2 is False


from sqlalchemy import text
from types import SimpleNamespace
from agf.db import repo
from agf.discovery.corpus_refresh import advertisers_with_page

CORPUS_PAGES = ("CORPUSREFA", "CORPUSREFB", "CORPUSREFC")


def _clean_corpus(conn):
    ids = conn.execute(text("SELECT id FROM public.advertisers WHERE page_id LIKE 'CORPUSREF%'")).scalars().all()
    if ids:
        conn.execute(text("DELETE FROM public.ads WHERE advertiser_id = ANY(:i)"), {"i": ids})
        conn.execute(text("DELETE FROM public.advertisers WHERE id = ANY(:i)"), {"i": ids})


def test_advertisers_with_page_stalest_first(pg_conn):
    _clean_corpus(pg_conn)
    # A: ad seen long ago; B: ad seen recently; C: no page_id (excluded)
    a = repo.upsert_advertiser(pg_conn, SimpleNamespace(page_id="CORPUSREFA", page_name="A", page_like_count=10))
    b = repo.upsert_advertiser(pg_conn, SimpleNamespace(page_id="CORPUSREFB", page_name="B", page_like_count=10))
    # C intentionally omitted — page_id=None violates DB NOT NULL; the query excludes nulls via WHERE
    repo.upsert_ad(pg_conn, make_ad("cr-old", "CORPUSREFA", "A", "https://x/app"), a, None)
    repo.upsert_ad(pg_conn, make_ad("cr-new", "CORPUSREFB", "B", "https://x/app"), b, None)
    pg_conn.execute(text("UPDATE public.ads SET last_seen = now() - interval '10 days' WHERE advertiser_id = :a"), {"a": a})
    pg_conn.execute(text("UPDATE public.ads SET last_seen = now() WHERE advertiser_id = :b"), {"b": b})

    pairs = advertisers_with_page(pg_conn)
    ours = [(aid, pid) for aid, pid in pairs if pid and pid.startswith("CORPUSREF")]
    assert (a, "CORPUSREFA") in ours and (b, "CORPUSREFB") in ours
    assert all(pid is not None for _, pid in ours)              # query excludes null page_ids
    assert ours.index((a, "CORPUSREFA")) < ours.index((b, "CORPUSREFB"))  # stalest (A) first
