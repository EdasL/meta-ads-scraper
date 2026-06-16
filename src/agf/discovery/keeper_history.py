"""Stage 3 of breadth-first discovery: backfill keeper ad history.

Triage promotes keepers into advertisers/apps but stores NO ads, so the weekly
velocity series (adtech.app_advertiser_metrics) never builds and a keeper can never
pass the scoring persistence gate (weeks_observed >= 2). This stage closes that gap:
for each kept candidate whose advertiser has zero metric rows, one page-scoped
active_status="ALL" pull returns the page's ad history WITH end dates, so we store
the ads and reconstruct the last `weeks` weekly active/new-ad series in one shot —
the proven scripts/persist_candidates.py path, productionized.

Week starts are Monday-aligned (the same convention as tracker.snapshots'
snapshot_advertiser_week and workers.main step 1.5), so the reconstructed series and
the live weekly rollup upsert the same (advertiser_id, week_start) rows.
"""
import time
from datetime import date, timedelta

from sqlalchemy import text

from agf.db import repo
from agf.db.engine import begin
from agf.extract.errors import ExtractError
from agf.tracker.history import new_ads_per_week, reconstruct_weekly_ad_counts


def keepers_missing_history(conn) -> list[tuple[int, str]]:
    """(advertiser_id, page_id) of kept candidates whose advertiser has ZERO rows in
    adtech.app_advertiser_metrics — the unscoreable keepers. Oldest triaged first."""
    return [(r[0], r[1]) for r in conn.execute(text("""
        SELECT c.kept_advertiser_id, a.page_id
        FROM discovery.candidates c
        JOIN public.advertisers a ON a.id = c.kept_advertiser_id
        WHERE c.status = 'kept' AND c.kept_advertiser_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM adtech.app_advertiser_metrics m
                          WHERE m.advertiser_id = c.kept_advertiser_id)
        GROUP BY c.kept_advertiser_id, a.page_id
        ORDER BY MIN(c.triaged_at), c.kept_advertiser_id
    """)).all()]


def _week_starts(today: date, n: int) -> list[date]:
    """n Monday-aligned week_start dates, oldest -> newest (newest = this week's
    Monday) — mirrors scripts/persist_candidates.py and step 1.5's week_start."""
    monday = today - timedelta(days=today.weekday())
    return [monday - timedelta(weeks=(n - 1 - i)) for i in range(n)]


def _linked_app_id(conn, advertiser_id: int) -> int | None:
    """The public.apps id linked via the keeper's candidate row (store, store_id).
    None when the app row is missing — upsert_ad accepts a NULL app_id."""
    return conn.execute(text("""
        SELECT ap.id
        FROM discovery.candidates c
        JOIN public.apps ap ON ap.store = c.store AND ap.store_id = c.store_id
        WHERE c.kept_advertiser_id = :adv AND c.status = 'kept'
        ORDER BY c.triaged_at, c.id
        LIMIT 1
    """), {"adv": advertiser_id}).scalar()


async def refresh_one_advertiser(engine, extractor, advertiser_id, page_id, *,
                                hist_pages: int = 12, weeks: int = 16,
                                flip_vanished: bool = False) -> dict:
    """Re-scrape one advertiser's Meta ALL-listing and reconcile public.ads in one txn:
    upsert every ad (is_active/start_date/end_date/last_seen via repo.upsert_ad) + its
    text-creative backstop, then reconstruct the last `weeks` weekly metric rows. When
    flip_vanished AND the listing was NOT truncated, flip the advertiser's still-active
    DB ads that are absent from the listing to is_active=false (they ended-and-delisted).
    Raises ExtractError if the listing pull fails (caller counts it). Returns
    {ads_stored, truncated, flipped}."""
    ads, truncated = await extractor.search_listing(page_id=page_id, max_pages=hist_pages)
    seen, uniq = set(), []
    for ad in ads:
        if ad.ad_archive_id and ad.ad_archive_id not in seen:
            seen.add(ad.ad_archive_id)
            uniq.append(ad)
    weekly = reconstruct_weekly_ad_counts(uniq, weeks=weeks)
    new_weekly = new_ads_per_week(uniq, weeks=weeks)
    starts = _week_starts(date.today(), weeks)
    flipped = 0
    with begin(engine) as conn:
        app_id = _linked_app_id(conn, advertiser_id)
        for ad in uniq:
            ad_id = repo.upsert_ad(conn, ad, advertiser_id, app_id)
            repo.ensure_text_creative(conn, ad_id, ad)
        for wk_start, cnt, nnew in zip(starts, weekly, new_weekly):
            repo.upsert_app_advertiser_metric(conn, advertiser_id, wk_start,
                                              cnt, max(nnew, 1), None)
        if flip_vanished and not truncated:
            flipped = conn.execute(text("""
                UPDATE public.ads
                   SET is_active = false,
                       end_date  = COALESCE(end_date, last_seen)
                 WHERE advertiser_id = :adv AND is_active
                   AND external_ad_id <> ALL(:seen)
            """), {"adv": advertiser_id,
                   "seen": [str(s) for s in seen]}).rowcount
    return {"ads_stored": len(uniq), "truncated": truncated, "flipped": flipped}


async def pull_keeper_history(engine, extractor, *, hist_pages: int = 12,
                              budget_requests: int = 600, weeks: int = 16,
                              sleep=time.sleep, kw_delay: float = 2.0) -> dict:
    """Backfill ads + the weekly metric series for every keeper missing history,
    under a proxy-request budget: the loop stops BEFORE a keeper whose `hist_pages`
    pull would exceed `budget_requests` (requests_spent is the page upper bound;
    search may stop earlier). Per keeper, in one transaction: upsert each deduped ad
    (+ text-creative backstop) and upsert the reconstructed series for the last
    `weeks` Monday-aligned weeks. An ExtractError on one keeper counts as an error
    and the loop moves on. Returns
    {keepers_done, requests_spent, ads_stored, errors}."""
    with engine.connect() as conn:
        keepers = keepers_missing_history(conn)
    stats = {"keepers_done": 0, "requests_spent": 0, "ads_stored": 0, "errors": 0}
    for advertiser_id, page_id in keepers:
        if stats["requests_spent"] + hist_pages > budget_requests:
            break
        stats["requests_spent"] += hist_pages
        try:
            res = await refresh_one_advertiser(engine, extractor, advertiser_id,
                                               page_id, hist_pages=hist_pages, weeks=weeks)
        except ExtractError:
            stats["errors"] += 1
            sleep(kw_delay)
            continue
        stats["keepers_done"] += 1
        stats["ads_stored"] += res["ads_stored"]
        sleep(kw_delay)
    return stats
