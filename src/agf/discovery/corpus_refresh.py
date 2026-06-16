"""Corpus-wide ad refresh: re-scrape EVERY advertiser's live Meta listing (not just
unscored keepers) so GemScore v3 running-days stays accurate. Reuses
keeper_history.refresh_one_advertiser per advertiser; budget-bounded + paced like the
keeper backfill. One-shot operator path (scripts/refresh_corpus_ads.py)."""
import time

from sqlalchemy import text

from agf.extract.errors import ExtractError
from agf.discovery.keeper_history import refresh_one_advertiser


def advertisers_with_page(conn) -> list[tuple[int, str]]:
    """(advertiser_id, page_id) for every advertiser with a non-null page_id, stalest
    first (their freshest ad's last_seen ascending; NULL = never scraped = first), so a
    budget-limited or interrupted run refreshes the most-stale advertisers first."""
    return [(r[0], r[1]) for r in conn.execute(text("""
        SELECT a.id, a.page_id
        FROM public.advertisers a
        JOIN public.ads ad ON ad.advertiser_id = a.id
        WHERE a.page_id IS NOT NULL
        GROUP BY a.id, a.page_id
        ORDER BY MAX(ad.last_seen) ASC NULLS FIRST, a.id
    """)).all()]


async def refresh_corpus(engine, extractor, *, hist_pages: int = 12,
                         budget_requests: int = 12_000, weeks: int = 16,
                         kw_delay: float = 2.0, sleep=time.sleep,
                         offset: int = 0, limit: int | None = None) -> dict:
    """Re-scrape the whole advertiser corpus (stalest first) under a proxy-request
    budget, flipping vanished ads on complete listings. The loop stops BEFORE an
    advertiser whose hist_pages pull would exceed budget_requests. offset/limit slice
    the target list (probe/resume). Returns
    {advertisers_done, requests_spent, ads_stored, flipped, truncated, errors}."""
    with engine.connect() as conn:
        targets = advertisers_with_page(conn)
    targets = targets[offset:]
    if limit is not None:
        targets = targets[:limit]
    stats = {"advertisers_done": 0, "requests_spent": 0, "ads_stored": 0,
             "flipped": 0, "truncated": 0, "errors": 0}
    for advertiser_id, page_id in targets:
        if stats["requests_spent"] + hist_pages > budget_requests:
            break
        stats["requests_spent"] += hist_pages
        try:
            res = await refresh_one_advertiser(engine, extractor, advertiser_id, page_id,
                                               hist_pages=hist_pages, weeks=weeks,
                                               flip_vanished=True)
        except ExtractError:
            stats["errors"] += 1
            sleep(kw_delay)
            continue
        stats["advertisers_done"] += 1
        stats["ads_stored"] += res["ads_stored"]
        stats["flipped"] += res["flipped"]
        stats["truncated"] += 1 if res["truncated"] else 0
        sleep(kw_delay)
    return stats
