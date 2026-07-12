"""Stage-3 keeper history: missing-history detection, budgeted page pulls, and the
reconstructed weekly series landing in adtech.app_advertiser_metrics.

KEEPERHIST* sentinels. The engine tests COMMIT (pull_keeper_history owns its own
transactions), so every seed is cleaned in finally and every assertion is scoped to
the sentinels. Seeds get an ancient triaged_at so they sit at the head of the
oldest-first queue — a shared DB's stray keepers can't consume the test budget."""
import asyncio
import time
from datetime import date, timedelta
from types import SimpleNamespace

from sqlalchemy import text

from agf.db import repo
from agf.db.candidates import mark_triaged, upsert_sweep_candidate
from agf.discovery.keeper_history import keepers_missing_history, pull_keeper_history
from agf.extract.errors import ExtractError
from agf.parse import AdRecord
from tests.discovery.conftest import FakeExtractor

DAY = 86_400
PAGES = ("KEEPERHIST1", "KEEPERHIST2", "KEEPERHIST3")
STORE_IDS = ("771001", "771002", "771003")


def hist_ad(archive, page_id, *, days_old, end_days_ago=None, now=None):
    """An AdRecord whose run spans [now - days_old, now - end_days_ago] (end None =
    still active). Offsets stay off 7-day multiples so week-window math is stable."""
    now = time.time() if now is None else now
    return AdRecord(archive, page_id, "Keeper Hist", end_days_ago is None,
                    int(now - days_old * DAY),
                    int(now - end_days_ago * DAY) if end_days_ago is not None else None,
                    1, "INSTALL_MOBILE_APP", "Install",
                    "https://apps.apple.com/us/app/x/id771001", "body")


def _seed_keeper(conn, page_id, store_id, *, triaged_at="2000-01-01"):
    """Promote one kept candidate exactly like triage does (advertiser + app, no ads)."""
    adv_id = repo.upsert_advertiser(conn, SimpleNamespace(
        page_id=page_id, page_name="Keeper Hist", page_like_count=900))
    repo.upsert_app(conn, {"store": "ios", "store_id": store_id, "name": "Keeper App"})
    cid, _ = upsert_sweep_candidate(conn, dict(
        page_id=page_id, page_name="Keeper Hist", page_like_count=900,
        store="ios", store_id=store_id, link_url="https://x", cta_type=None,
        ad_start_date=None, source_keyword="k", technique="t"))
    mark_triaged(conn, cid, status="kept", kept_advertiser_id=adv_id)
    conn.execute(text("UPDATE discovery.candidates SET triaged_at=:t WHERE id=:i"),
                 {"t": triaged_at, "i": cid})
    return adv_id


def _clean(eng):
    with eng.begin() as conn:
        # candidates reference advertisers (kept_advertiser_id FK) — clear them first.
        conn.execute(text(
            "DELETE FROM discovery.candidates WHERE page_id LIKE 'KEEPERHIST%'"))
        ids = conn.execute(text(
            "SELECT id FROM public.advertisers WHERE page_id LIKE 'KEEPERHIST%'"
        )).scalars().all()
        if ids:
            conn.execute(text("DELETE FROM logging.crawl_log WHERE "
                              "entity_type='keeper_history' AND entity_id = ANY(:i)"),
                         {"i": ids})
            conn.execute(text("DELETE FROM adtech.app_advertiser_metrics "
                              "WHERE advertiser_id = ANY(:i)"), {"i": ids})
            conn.execute(text("DELETE FROM public.creatives c USING public.ads a "
                              "WHERE c.ad_id = a.id AND a.advertiser_id = ANY(:i)"),
                         {"i": ids})
            conn.execute(text("DELETE FROM public.ads WHERE advertiser_id = ANY(:i)"),
                         {"i": ids})
            conn.execute(text("DELETE FROM public.advertisers WHERE id = ANY(:i)"),
                         {"i": ids})
        conn.execute(text("DELETE FROM public.apps WHERE store='ios' "
                          "AND store_id = ANY(:s)"), {"s": list(STORE_IDS)})


# --- detection ----------------------------------------------------------------
def test_missing_history_detection(pg_conn):
    a1 = _seed_keeper(pg_conn, PAGES[0], STORE_IDS[0], triaged_at="2000-01-01")
    a2 = _seed_keeper(pg_conn, PAGES[1], STORE_IDS[1], triaged_at="2000-01-02")
    a3 = _seed_keeper(pg_conn, PAGES[2], STORE_IDS[2], triaged_at="2000-01-03")
    # a2 already has a weekly metric row -> NOT missing history
    repo.upsert_app_advertiser_metric(pg_conn, a2, date(2026, 6, 1), 3, 1, None)
    got = [t for t in keepers_missing_history(pg_conn) if t[0] in (a1, a2, a3)]
    assert got == [(a1, PAGES[0]), (a3, PAGES[2])]      # kept w/o metrics, oldest first


# --- due selection (TTL failure suppression) ----------------------------------
def test_keepers_due_excludes_fresh_crawl_log(_migrated_engine):
    # Separate committed txns so each now() advances — mark_crawled writes a
    # clock_timestamp() row and the ttl_hours=0 assertion needs the marker to be
    # strictly in the past relative to a later query's now().
    from agf.discovery.keeper_history import keepers_due_for_history
    from agf.workers.scheduler import mark_crawled
    eng = _migrated_engine
    _clean(eng)
    try:
        with eng.begin() as conn:
            adv_id = _seed_keeper(conn, PAGES[0], STORE_IDS[0], triaged_at="2000-01-01")
        with eng.connect() as conn:
            due = [a for a, _pid in keepers_due_for_history(conn)]
        assert adv_id in due
        with eng.begin() as conn:
            mark_crawled(conn, "keeper_history", adv_id, "error:ExtractError")
        with eng.connect() as conn:
            due = [a for a, _pid in keepers_due_for_history(conn)]
        assert adv_id not in due                    # TTL suppression
        with eng.connect() as conn:
            due0 = [a for a, _pid in keepers_due_for_history(conn, ttl_hours=0)]
        assert adv_id in due0                        # ttl 0 = everything due
    finally:
        _clean(eng)


def test_keepers_due_respects_limit(_migrated_engine):
    from agf.discovery.keeper_history import keepers_due_for_history
    eng = _migrated_engine
    _clean(eng)
    try:
        with eng.begin() as conn:
            _seed_keeper(conn, PAGES[0], STORE_IDS[0], triaged_at="2000-01-01")
            _seed_keeper(conn, PAGES[1], STORE_IDS[1], triaged_at="2000-01-02")
        with eng.connect() as conn:
            assert len(keepers_due_for_history(conn, limit=1)) <= 1
    finally:
        _clean(eng)


# --- budget -------------------------------------------------------------------
def test_budget_stops_before_exceeding(_migrated_engine):
    eng = _migrated_engine
    _clean(eng)
    try:
        with eng.begin() as conn:
            a1 = _seed_keeper(conn, PAGES[0], STORE_IDS[0], triaged_at="2000-01-01")
            a2 = _seed_keeper(conn, PAGES[1], STORE_IDS[1], triaged_at="2000-01-02")
        ext = FakeExtractor(page_ads={
            PAGES[0]: [hist_ad("kh-b1", PAGES[0], days_old=10)],
            PAGES[1]: [hist_ad("kh-b2", PAGES[1], days_old=10)],
        })
        stats = asyncio.run(pull_keeper_history(
            eng, ext, hist_pages=12, budget_requests=12, sleep=lambda s: None))
        assert stats["keepers_done"] == 1 and stats["requests_spent"] == 12
        with eng.connect() as conn:
            n1, n2, m2 = conn.execute(text(
                "SELECT (SELECT count(*) FROM public.ads WHERE advertiser_id=:a1), "
                "       (SELECT count(*) FROM public.ads WHERE advertiser_id=:a2), "
                "       (SELECT count(*) FROM adtech.app_advertiser_metrics "
                "        WHERE advertiser_id=:a2)"), {"a1": a1, "a2": a2}).one()
        assert n1 == 1                      # oldest-triaged keeper was processed
        assert n2 == 0 and m2 == 0          # the second would exceed the budget
    finally:
        _clean(eng)


# --- ads + reconstructed weekly series ----------------------------------------
def test_ads_and_weekly_series_written(_migrated_engine):
    eng = _migrated_engine
    _clean(eng)
    try:
        with eng.begin() as conn:
            adv = _seed_keeper(conn, PAGES[0], STORE_IDS[0])
        now = time.time()
        ext = FakeExtractor(page_ads={PAGES[0]: [
            hist_ad("kh-a", PAGES[0], days_old=30, now=now),                  # active since wk 11
            hist_ad("kh-c", PAGES[0], days_old=29, now=now),                  # same start week
            hist_ad("kh-b", PAGES[0], days_old=17, end_days_ago=3, now=now),  # ran wks 13..15
            hist_ad("kh-a", PAGES[0], days_old=30, now=now),                  # dup -> deduped
        ]})
        stats = asyncio.run(pull_keeper_history(
            eng, ext, hist_pages=12, budget_requests=12, weeks=16, sleep=lambda s: None))
        assert stats == {"keepers_done": 1, "requests_spent": 12,
                         "ads_stored": 3, "errors": 0}
        with eng.connect() as conn:
            ad_rows = conn.execute(text(
                "SELECT external_ad_id, is_active, app_id FROM public.ads "
                "WHERE advertiser_id=:a ORDER BY external_ad_id"), {"a": adv}).all()
            creatives = conn.execute(text(
                "SELECT count(*) FROM public.creatives c JOIN public.ads a ON a.id=c.ad_id "
                "WHERE a.advertiser_id=:a"), {"a": adv}).scalar()
            app_id = conn.execute(text(
                "SELECT id FROM public.apps WHERE store='ios' AND store_id=:s"),
                {"s": STORE_IDS[0]}).scalar()
            metrics = conn.execute(text(
                "SELECT week_start, active_ad_count, distinct_creative_count "
                "FROM adtech.app_advertiser_metrics WHERE advertiser_id=:a "
                "ORDER BY week_start"), {"a": adv}).all()
        assert [r.external_ad_id for r in ad_rows] == ["kh-a", "kh-b", "kh-c"]
        assert all(r.app_id == app_id for r in ad_rows)       # linked via candidate store_id
        assert creatives == 3                                 # text-creative backstop per ad
        # 16 Monday-aligned weeks, newest = this week's Monday (snapshot convention)
        assert len(metrics) == 16
        monday = date.today() - timedelta(days=date.today().weekday())
        assert [m.week_start for m in metrics] == \
            [monday - timedelta(weeks=15 - i) for i in range(16)]
        # the counts reconstruct the fake ads' date spans
        assert [m.active_ad_count for m in metrics] == [0] * 11 + [2, 2, 3, 3, 3]
        assert [m.distinct_creative_count for m in metrics] == [1] * 11 + [2, 1, 1, 1, 1]
    finally:
        _clean(eng)


# --- error isolation ------------------------------------------------------------
def test_extract_error_counts_and_continues(_migrated_engine):
    class BoomExtractor(FakeExtractor):
        async def search(self, **kw):
            if kw.get("view_all_page_id") == PAGES[0]:
                raise ExtractError("throttled")
            return await super().search(**kw)

    eng = _migrated_engine
    _clean(eng)
    try:
        with eng.begin() as conn:
            _seed_keeper(conn, PAGES[0], STORE_IDS[0], triaged_at="2000-01-01")
            a2 = _seed_keeper(conn, PAGES[1], STORE_IDS[1], triaged_at="2000-01-02")
        ext = BoomExtractor(page_ads={PAGES[1]: [hist_ad("kh-e2", PAGES[1], days_old=10)]})
        stats = asyncio.run(pull_keeper_history(
            eng, ext, hist_pages=12, budget_requests=24, sleep=lambda s: None))
        assert stats["errors"] == 1 and stats["keepers_done"] == 1
        assert stats["ads_stored"] == 1
        with eng.connect() as conn:
            n2 = conn.execute(text("SELECT count(*) FROM public.ads "
                                   "WHERE advertiser_id=:a"), {"a": a2}).scalar()
        assert n2 == 1                       # the keeper after the failure still ran
    finally:
        _clean(eng)
