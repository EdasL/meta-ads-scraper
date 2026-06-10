"""Per-advertiser growth summary (master-plan Track A2). One set-based window pass
over adtech.app_advertiser_metrics computes first/latest active_ad_count (ordered by
week_start) and the number of weeks observed, then upserts adtech.advertiser_growth.
conn-first: never commits — the caller owns the transaction (wired into run_scoring
in the same txn as score_all, before the matview refresh)."""
from sqlalchemy import text
from agf.db import repo


def compute_advertiser_growth(conn, only_ids=None) -> int:
    """Roll the weekly active-ad-count series into a growth summary per advertiser.

    pct_change rules (computed in SQL): 1 week observed → NULL; never advertised
    (no non-zero week) → NULL; else round(100*(latest-base)/base, 1) where `base` is the
    first NON-ZERO week's count. Anchoring on the first non-zero week (not the literal
    earliest week) lets a "from zero" ramp (0→N — the strongest emerging signal) report a
    real % instead of NULL-by-divide-by-zero. first_count still records the literal earliest
    week for the "started at" display.

    Production passes only_ids=None → recomputes EVERY advertiser with a weekly series.
    only_ids (an advertiser-id allowlist) restricts the recompute to those advertisers —
    used by tests/harnesses so a shared DB's other rows are never recomputed. Returns
    rows upserted."""
    params: dict = {}
    adv_filter = ""
    if only_ids is not None:
        adv_filter = " WHERE advertiser_id = ANY(:ids)"
        params["ids"] = list(only_ids)
    rows = conn.execute(text(f"""
        WITH agg AS (
            SELECT advertiser_id,
                   (array_agg(active_ad_count ORDER BY week_start))[1]                  AS first_count,
                   (array_agg(active_ad_count ORDER BY week_start DESC))[1]             AS latest_count,
                   (array_agg(active_ad_count ORDER BY week_start)
                        FILTER (WHERE active_ad_count > 0))[1]                          AS first_nonzero,
                   count(*)                                                             AS weeks_observed
            FROM adtech.app_advertiser_metrics{adv_filter}
            GROUP BY advertiser_id
        )
        SELECT advertiser_id, first_count, latest_count, weeks_observed,
               CASE
                   WHEN weeks_observed <= 1 THEN NULL
                   WHEN first_nonzero IS NULL THEN NULL          -- never advertised
                   ELSE round((100.0 * (latest_count - first_nonzero)
                               / first_nonzero)::numeric, 1)
               END AS pct_change
        FROM agg
    """), params).all()
    for advertiser_id, first_count, latest_count, weeks_observed, pct_change in rows:
        repo.upsert_advertiser_growth(
            conn, advertiser_id, first_count, latest_count, weeks_observed,
            None if pct_change is None else float(pct_change))
    return len(rows)
