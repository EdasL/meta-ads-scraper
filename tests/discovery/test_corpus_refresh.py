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


def test_meta_rank_follows_listing_order(pg_conn, _migrated_engine):
    _clean_corpus(pg_conn)
    eng = _migrated_engine
    adv = repo.upsert_advertiser(pg_conn, SimpleNamespace(page_id="CORPUSREFR", page_name="R", page_like_count=10))
    pg_conn.commit()
    # listing returns three ads in impressions order: top, mid, low.
    listing = [make_ad("cr-top", "CORPUSREFR", "R", "https://x/app"),
               make_ad("cr-mid", "CORPUSREFR", "R", "https://x/app"),
               make_ad("cr-low", "CORPUSREFR", "R", "https://x/app")]
    ext = FakeExtractor(page_ads={"CORPUSREFR": listing}, page_truncated={"CORPUSREFR": False})
    asyncio.run(refresh_one_advertiser(eng, ext, adv, "CORPUSREFR", hist_pages=12, weeks=16))
    ranks = {r.external_ad_id: r.meta_rank for r in pg_conn.execute(text(
        "SELECT external_ad_id, meta_rank FROM public.ads WHERE advertiser_id = :a"), {"a": adv})}
    assert ranks == {"cr-top": 1, "cr-mid": 2, "cr-low": 3}, ranks


from agf.discovery.corpus_refresh import refresh_corpus


def test_refresh_corpus_refreshes_each_and_respects_budget(pg_conn, _migrated_engine):
    _clean_corpus(pg_conn)
    eng = _migrated_engine
    a = repo.upsert_advertiser(pg_conn, SimpleNamespace(page_id="CORPUSREFA", page_name="A", page_like_count=10))
    b = repo.upsert_advertiser(pg_conn, SimpleNamespace(page_id="CORPUSREFB", page_name="B", page_like_count=10))
    repo.upsert_ad(pg_conn, make_ad("cr-A1", "CORPUSREFA", "A", "https://x/app"), a, None)
    repo.upsert_ad(pg_conn, make_ad("cr-B1", "CORPUSREFB", "B", "https://x/app"), b, None)
    # A is ancient so it is DETERMINISTICALLY the stalest in the shared test DB (the
    # budget=12 run processes exactly the one stalest advertiser); B is recent.
    pg_conn.execute(text("UPDATE public.ads SET last_seen = '2000-01-01' WHERE advertiser_id = :a"), {"a": a})
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


def test_flip_skipped_when_listing_empty(pg_conn, _migrated_engine):
    # A non-truncated EMPTY listing must NOT mass-flip the advertiser's ads: a transient
    # empty/malformed 200 parses as [] and must not wipe running-days. (reviewer fix)
    _clean_corpus(pg_conn)
    eng = _migrated_engine
    adv = repo.upsert_advertiser(pg_conn, SimpleNamespace(page_id="CORPUSREFC", page_name="C", page_like_count=10))
    repo.upsert_ad(pg_conn, make_ad("cr-C1", "CORPUSREFC", "C", "https://x/app"), adv, None)
    repo.upsert_ad(pg_conn, make_ad("cr-C2", "CORPUSREFC", "C", "https://x/app"), adv, None)
    pg_conn.commit()
    # no page_ads entry for CORPUSREFC → listing returns [] ; not truncated
    ext = FakeExtractor(page_ads={}, page_truncated={"CORPUSREFC": False})
    res = asyncio.run(refresh_one_advertiser(eng, ext, adv, "CORPUSREFC",
                                             hist_pages=12, weeks=16, flip_vanished=True))
    assert res["flipped"] == 0
    active = {r.external_ad_id: r.is_active for r in pg_conn.execute(text(
        "SELECT external_ad_id, is_active FROM public.ads WHERE advertiser_id = :a"), {"a": adv})}
    assert active == {"cr-C1": True, "cr-C2": True}   # NOT flipped — empty listing is not trusted


from agf.discovery.corpus_refresh import corpus_due_for_refresh
from agf.workers.scheduler import mark_crawled


def test_corpus_due_stalest_first_with_staleness_floor(pg_conn):
    _clean_corpus(pg_conn)
    a = repo.upsert_advertiser(pg_conn, SimpleNamespace(page_id="CORPUSREFA", page_name="A", page_like_count=10))
    b = repo.upsert_advertiser(pg_conn, SimpleNamespace(page_id="CORPUSREFB", page_name="B", page_like_count=10))
    r = repo.upsert_advertiser(pg_conn, SimpleNamespace(page_id="CORPUSREFR", page_name="R", page_like_count=10))
    repo.upsert_ad(pg_conn, make_ad("cr-A1", "CORPUSREFA", "A", "https://x/app"), a, None)
    repo.upsert_ad(pg_conn, make_ad("cr-B1", "CORPUSREFB", "B", "https://x/app"), b, None)
    repo.upsert_ad(pg_conn, make_ad("cr-R1", "CORPUSREFR", "R", "https://x/app"), r, None)
    # A stalest, B stale, R fresh (within the floor → NOT due even though queue is empty)
    pg_conn.execute(text("UPDATE public.ads SET last_seen = now() - interval '10 days' WHERE advertiser_id = :i"), {"i": a})
    pg_conn.execute(text("UPDATE public.ads SET last_seen = now() - interval '4 days' WHERE advertiser_id = :i"), {"i": b})
    pg_conn.execute(text("UPDATE public.ads SET last_seen = now() - interval '1 hour' WHERE advertiser_id = :i"), {"i": r})

    due = [p for p in corpus_due_for_refresh(pg_conn, stale_hours=48, err_ttl_hours=168, limit=100)
           if p[1].startswith("CORPUSREF")]
    assert due[0] == (a, "CORPUSREFA")               # stalest first
    assert (b, "CORPUSREFB") in due
    assert all(aid != r for aid, _ in due)           # fresh advertiser not due

    # limit respected, order preserved
    due1 = [p for p in corpus_due_for_refresh(pg_conn, stale_hours=48, err_ttl_hours=168, limit=1)]
    assert due1 == [(a, "CORPUSREFA")]


def test_corpus_due_suppresses_recent_error_marker(pg_conn):
    _clean_corpus(pg_conn)
    a = repo.upsert_advertiser(pg_conn, SimpleNamespace(page_id="CORPUSREFA", page_name="A", page_like_count=10))
    b = repo.upsert_advertiser(pg_conn, SimpleNamespace(page_id="CORPUSREFB", page_name="B", page_like_count=10))
    repo.upsert_ad(pg_conn, make_ad("cr-A1", "CORPUSREFA", "A", "https://x/app"), a, None)
    repo.upsert_ad(pg_conn, make_ad("cr-B1", "CORPUSREFB", "B", "https://x/app"), b, None)
    pg_conn.execute(text("UPDATE public.ads SET last_seen = now() - interval '10 days' WHERE advertiser_id = ANY(:i)"), {"i": [a, b]})
    # A errored recently → suppressed; B errored beyond the TTL → due again
    mark_crawled(pg_conn, "corpus_refresh", a, "error:TimeoutError")
    mark_crawled(pg_conn, "corpus_refresh", b, "error:TimeoutError")
    pg_conn.execute(text(
        "UPDATE logging.crawl_log SET crawled_at = now() - interval '30 days' "
        "WHERE entity_type = 'corpus_refresh' AND entity_id = :i"), {"i": b})

    due_ids = [aid for aid, pid in corpus_due_for_refresh(pg_conn, stale_hours=48, err_ttl_hours=168, limit=100)
               if pid.startswith("CORPUSREF")]
    assert a not in due_ids                          # fresh error marker suppresses
    assert b in due_ids                              # expired marker: due again
    # a keeper_history marker must NOT suppress corpus refresh (kinds are distinct)
    mark_crawled(pg_conn, "keeper_history", b, "error:TimeoutError")
    due_ids = [aid for aid, pid in corpus_due_for_refresh(pg_conn, stale_hours=48, err_ttl_hours=168, limit=100)
               if pid.startswith("CORPUSREF")]
    assert b in due_ids


def test_corpus_due_suppresses_recent_success_marker(pg_conn):
    """A DEAD advertiser (no live ads in the listing) never bumps ads.last_seen, so
    the 'ok' crawl_log marker is the only progress stamp — without this suppression
    the stalest-first queue hot-loops the same dead advertisers every tick
    (caught live 2026-07-13: 16 done jobs, freshest_ad still a month old)."""
    _clean_corpus(pg_conn)
    a = repo.upsert_advertiser(pg_conn, SimpleNamespace(page_id="CORPUSREFA", page_name="A", page_like_count=10))
    b = repo.upsert_advertiser(pg_conn, SimpleNamespace(page_id="CORPUSREFB", page_name="B", page_like_count=10))
    repo.upsert_ad(pg_conn, make_ad("cr-A1", "CORPUSREFA", "A", "https://x/app"), a, None)
    repo.upsert_ad(pg_conn, make_ad("cr-B1", "CORPUSREFB", "B", "https://x/app"), b, None)
    pg_conn.execute(text("UPDATE public.ads SET last_seen = now() - interval '30 days' WHERE advertiser_id = ANY(:i)"), {"i": [a, b]})
    # A refreshed OK just now (dead page — ads unchanged); B refreshed OK 3 days ago
    mark_crawled(pg_conn, "corpus_refresh", a, "ok")
    mark_crawled(pg_conn, "corpus_refresh", b, "ok")
    pg_conn.execute(text(
        "UPDATE logging.crawl_log SET crawled_at = now() - interval '3 days' "
        "WHERE entity_type = 'corpus_refresh' AND entity_id = :i"), {"i": b})

    due_ids = [aid for aid, pid in corpus_due_for_refresh(pg_conn, stale_hours=48, err_ttl_hours=168, limit=100)
               if pid.startswith("CORPUSREF")]
    assert a not in due_ids                          # fresh 'ok' marker suppresses
    assert b in due_ids                              # marker older than the floor: due again


def test_refresh_corpus_beats_heartbeat_per_advertiser(pg_conn, _migrated_engine):
    # The nightly corpus refresh runs longer than the watchdog's staleness budget
    # (2026-07-11 crash-loop after the tick halved to 1800s shrank the budget to
    # 65min): every long stage must beat the heartbeat per unit of work.
    _clean_corpus(pg_conn)
    eng = _migrated_engine
    a = repo.upsert_advertiser(pg_conn, SimpleNamespace(page_id="CORPUSREFA", page_name="A", page_like_count=10))
    repo.upsert_ad(pg_conn, make_ad("cr-A1", "CORPUSREFA", "A", "https://x/app"), a, None)
    pg_conn.execute(text("UPDATE public.ads SET last_seen = '2000-01-01' WHERE advertiser_id = :a"), {"a": a})
    pg_conn.commit()
    ext = FakeExtractor(page_ads={
        "CORPUSREFA": [make_ad("cr-A1", "CORPUSREFA", "A", "https://x/app")],
    })
    beats = []
    res = asyncio.run(refresh_corpus(eng, ext, hist_pages=12, budget_requests=12,
                                     sleep=lambda s: None,
                                     heartbeat=lambda: beats.append(1)))
    assert res["advertisers_done"] == 1
    assert len(beats) >= 1                        # one beat per advertiser processed
