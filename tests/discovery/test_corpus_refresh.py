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
        ad_ids = conn.execute(text("SELECT id FROM public.ads WHERE advertiser_id = ANY(:i)"), {"i": ids}).scalars().all()
        if ad_ids:
            conn.execute(text("DELETE FROM public.creatives WHERE ad_id = ANY(:i)"), {"i": ad_ids})
        conn.execute(text("DELETE FROM public.ads WHERE advertiser_id = ANY(:i)"), {"i": ids})
        conn.execute(text("DELETE FROM adtech.app_advertiser_metrics WHERE advertiser_id = ANY(:i)"), {"i": ids})
        conn.execute(text("DELETE FROM adtech.advertiser_growth WHERE advertiser_id = ANY(:i)"), {"i": ids})
        conn.execute(text("DELETE FROM adtech.advertiser_explainers WHERE advertiser_id = ANY(:i)"), {"i": ids})
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


from agf.discovery.keeper_history import refresh_one_advertiser


def test_flip_marks_vanished_ad_inactive_when_complete(pg_conn, _migrated_engine):
    _clean_corpus(pg_conn)
    eng = _migrated_engine
    adv = repo.upsert_advertiser(pg_conn, SimpleNamespace(page_id="CORPUSREFA", page_name="A", page_like_count=10))
    # two active ads in DB; fresh listing returns ONLY A1 (A2 vanished/ended)
    repo.upsert_ad(pg_conn, make_ad("cr-A1", "CORPUSREFA", "A", "https://x/app"), adv, None)
    repo.upsert_ad(pg_conn, make_ad("cr-A2", "CORPUSREFA", "A", "https://x/app"), adv, None)
    pg_conn.execute(text("UPDATE public.ads SET last_seen = now() - interval '5 days' WHERE advertiser_id = :a"), {"a": adv})
    pg_conn.commit()

    ext = FakeExtractor(page_ads={"CORPUSREFA": [make_ad("cr-A1", "CORPUSREFA", "A", "https://x/app")]},
                        page_truncated={"CORPUSREFA": False})
    res = asyncio.run(refresh_one_advertiser(eng, ext, adv, "CORPUSREFA",
                                             hist_pages=12, weeks=16, flip_vanished=True))
    assert res["flipped"] == 1
    rows = {r.external_ad_id: (r.is_active, r.last_seen) for r in pg_conn.execute(text(
        "SELECT external_ad_id, is_active, last_seen FROM public.ads WHERE advertiser_id = :a"), {"a": adv})}
    assert rows["cr-A1"][0] is True                 # still in listing → active, last_seen bumped
    assert rows["cr-A1"][1] is not None
    assert rows["cr-A2"][0] is False                # vanished → flipped inactive


def test_flip_skipped_when_listing_truncated(pg_conn, _migrated_engine):
    _clean_corpus(pg_conn)
    eng = _migrated_engine
    adv = repo.upsert_advertiser(pg_conn, SimpleNamespace(page_id="CORPUSREFB", page_name="B", page_like_count=10))
    repo.upsert_ad(pg_conn, make_ad("cr-B1", "CORPUSREFB", "B", "https://x/app"), adv, None)
    repo.upsert_ad(pg_conn, make_ad("cr-B2", "CORPUSREFB", "B", "https://x/app"), adv, None)
    pg_conn.commit()
    # listing returns only B1 BUT is truncated → cannot trust absence of B2
    ext = FakeExtractor(page_ads={"CORPUSREFB": [make_ad("cr-B1", "CORPUSREFB", "B", "https://x/app")]},
                        page_truncated={"CORPUSREFB": True})
    res = asyncio.run(refresh_one_advertiser(eng, ext, adv, "CORPUSREFB",
                                             hist_pages=12, weeks=16, flip_vanished=True))
    assert res["truncated"] is True and res["flipped"] == 0
    active = {r.external_ad_id: r.is_active for r in pg_conn.execute(text(
        "SELECT external_ad_id, is_active FROM public.ads WHERE advertiser_id = :a"), {"a": adv})}
    assert active["cr-B2"] is True                  # NOT flipped — listing was incomplete


from agf.discovery.corpus_refresh import refresh_corpus


def test_refresh_corpus_refreshes_each_and_respects_budget(pg_conn, _migrated_engine):
    _clean_corpus(pg_conn)
    eng = _migrated_engine
    a = repo.upsert_advertiser(pg_conn, SimpleNamespace(page_id="CORPUSREFA", page_name="A", page_like_count=10))
    b = repo.upsert_advertiser(pg_conn, SimpleNamespace(page_id="CORPUSREFB", page_name="B", page_like_count=10))
    repo.upsert_ad(pg_conn, make_ad("cr-A1", "CORPUSREFA", "A", "https://x/app"), a, None)
    repo.upsert_ad(pg_conn, make_ad("cr-B1", "CORPUSREFB", "B", "https://x/app"), b, None)
    pg_conn.execute(text("UPDATE public.ads SET last_seen = now() - interval '9 days' WHERE advertiser_id = :a"), {"a": a})
    pg_conn.execute(text("UPDATE public.ads SET last_seen = now() - interval '1 days' WHERE advertiser_id = :b"), {"b": b})
    pg_conn.commit()

    ext = FakeExtractor(page_ads={
        "CORPUSREFA": [make_ad("cr-A1", "CORPUSREFA", "A", "https://x/app"),
                       make_ad("cr-A2", "CORPUSREFA", "A", "https://x/app", days_old=5)],
        "CORPUSREFB": [make_ad("cr-B1", "CORPUSREFB", "B", "https://x/app")],
    })
    # budget = one advertiser's pages → only the STALEST (A) is processed
    res = asyncio.run(refresh_corpus(eng, ext, hist_pages=12, budget_requests=12,
                                     sleep=lambda s: None, limit=None))
    assert res["advertisers_done"] == 1
    assert res["ads_stored"] == 2                   # A's two listing ads upserted
    a2 = pg_conn.execute(text("SELECT 1 FROM public.ads WHERE external_ad_id='cr-A2'")).first()
    assert a2 is not None                           # new ad A2 inserted for the stalest advertiser
