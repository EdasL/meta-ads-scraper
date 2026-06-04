"""Daily/weekly snapshots into the spine (master-plan §6 P5-T1/T2). conn-first:
never commits — the caller owns the transaction."""
from sqlalchemy import text
from agf.db import repo


async def snapshot_ad_metrics(conn, extractor, snapshot_date) -> int:
    """For each ACTIVE ad, fetch eu_total_reach + active via extractor.ad_details
    (§3.4) and record an ad_metrics row. Idempotent on (ad_id, snapshot_date).
    Returns the number of metrics written."""
    rows = conn.execute(text(
        "SELECT id, external_ad_id FROM public.ads WHERE is_active = TRUE "
        "ORDER BY id")).all()
    n = 0
    for ad_id, external_ad_id in rows:
        details = await extractor.ad_details(external_ad_id)
        repo.record_ad_metric(conn, ad_id, snapshot_date,
                              details.eu_total_reach, details.active)
        n += 1
    return n
