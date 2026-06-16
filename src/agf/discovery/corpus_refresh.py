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
        LEFT JOIN public.ads ad ON ad.advertiser_id = a.id
        WHERE a.page_id IS NOT NULL
        GROUP BY a.id, a.page_id
        ORDER BY MAX(ad.last_seen) ASC NULLS FIRST, a.id
    """)).all()]
