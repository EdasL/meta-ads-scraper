"""Daily/weekly snapshots into the spine (master-plan §6 P5-T1/T2). conn-first:
never commits — the caller owns the transaction."""
from datetime import timedelta
from sqlalchemy import text
from agf.db import repo
from agf.db.repo import _as_date   # reuse the date coercion helper


async def snapshot_ad_metrics(conn, extractor, snapshot_date, only_ids=None) -> int:
    """For each ACTIVE ad, fetch eu_total_reach + active via extractor.ad_details
    (§3.4) and record an ad_metrics row. Idempotent on (ad_id, snapshot_date).
    Returns the number of metrics written.

    Production passes only_ids=None → snapshots EVERY active ad (the daily tracker).
    only_ids (an ad-id allowlist) restricts the snapshot to those ads — used by tests so
    a committed row elsewhere in the shared DB doesn't inflate the count."""
    sql = "SELECT id, external_ad_id FROM public.ads WHERE is_active = TRUE"
    params: dict = {}
    if only_ids is not None:
        sql += " AND id = ANY(:ids)"
        params["ids"] = list(only_ids)
    rows = conn.execute(text(sql + " ORDER BY id"), params).all()
    n = 0
    for ad_id, external_ad_id in rows:
        details = await extractor.ad_details(external_ad_id)
        repo.record_ad_metric(conn, ad_id, snapshot_date,
                              details.eu_total_reach, details.active)
        n += 1
    return n


def snapshot_advertiser_week(conn, week_start, only_ids=None) -> int:
    """Roll the week [week_start, week_start+7) into app_advertiser_metrics:
    active ad count, distinct creatives, total EU reach (latest in-week snapshot
    per ad). DB-native analogue of scale.aggregate_advertisers. Idempotent on
    (advertiser_id, week_start). Returns rows upserted.

    Reach is aggregated in its own CTE (one row per ad) so the creative LEFT JOIN
    fan-out cannot double-count it.

    only_ids (an advertiser-id allowlist) restricts the rollup to those advertisers —
    the worker scopes each sweep to the advertisers it just confirmed, so the rollup
    only touches the tracked set (and a shared DB's other rows — demo, prior tests — are
    never recounted). only_ids=None rolls up every advertiser with active ads."""
    wk = _as_date(week_start)
    wk_end = wk + timedelta(days=7)
    params = {"wk": wk, "wk_end": wk_end}
    adv_filter = ""
    if only_ids is not None:
        adv_filter = " AND advertiser_id = ANY(:ids)"
        params["ids"] = list(only_ids)
    rows = conn.execute(text(f"""
        WITH active_ads AS (
            SELECT id, advertiser_id FROM public.ads
            WHERE is_active = TRUE AND advertiser_id IS NOT NULL{adv_filter}
        ),
        ad_reach AS (   -- latest in-week eu_total_reach per ad
            SELECT DISTINCT ON (m.ad_id) m.ad_id, m.eu_total_reach
            FROM adtech.ad_metrics m
            WHERE m.snapshot_date >= :wk AND m.snapshot_date < :wk_end
            ORDER BY m.ad_id, m.snapshot_date DESC
        ),
        per_adv AS (
            SELECT aa.advertiser_id,
                   count(DISTINCT aa.id) AS active_ad_count,
                   count(DISTINCT c.id)  AS distinct_creative_count
            FROM active_ads aa
            LEFT JOIN public.creatives c ON c.ad_id = aa.id
            GROUP BY aa.advertiser_id
        ),
        reach_adv AS (
            SELECT aa.advertiser_id, COALESCE(sum(ar.eu_total_reach), 0) AS total_eu_reach
            FROM active_ads aa
            LEFT JOIN ad_reach ar ON ar.ad_id = aa.id
            GROUP BY aa.advertiser_id
        )
        SELECT p.advertiser_id, p.active_ad_count, p.distinct_creative_count,
               COALESCE(r.total_eu_reach, 0) AS total_eu_reach
        FROM per_adv p
        LEFT JOIN reach_adv r ON r.advertiser_id = p.advertiser_id
    """), params).all()
    for advertiser_id, active_ad_count, distinct_creative_count, total_eu_reach in rows:
        repo.upsert_app_advertiser_metric(
            conn, advertiser_id, wk, active_ad_count,
            distinct_creative_count, int(total_eu_reach))
    return len(rows)
