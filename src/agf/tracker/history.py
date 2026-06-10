"""Reconstruct a page's week-over-week ad activity from its full ad history.

Live-probed 2026-06-10: a page-scoped pull with active_status=ALL returns INACTIVE ads too,
each with start_date AND end_date. So instead of waiting weeks to accumulate snapshots, we can
backfill the weekly active-ad-count curve from a single deep scrape — an ad counts as "active in
week W" if it started on/before the end of W and ended on/after the start of W.

Pure functions over AdRecord (start_date/end_date unix seconds, is_active). No I/O.
"""
import time as _time

DAY = 86400
WEEK = 7 * DAY


def _effective_end(ad, now: float) -> float | None:
    """End of an ad's run: its end_date, else now if still active, else its start (point)."""
    if ad.end_date:
        return float(ad.end_date)
    if ad.is_active:
        return now
    return float(ad.start_date) if ad.start_date else None


def reconstruct_weekly_ad_counts(ads, *, weeks: int = 16, now: float | None = None) -> list[int]:
    """Active-ad count for each of the last `weeks` weeks, oldest -> newest.

    Counts an ad in week W if its [start_date, effective_end] interval overlaps W.
    NOTE: when the page-scoped pull is paginated-capped, older weeks may be undercounted
    (the oldest ads were not fetched); the most recent weeks — what velocity cares about — are
    complete because the newest ads come first."""
    now = now if now is not None else _time.time()
    series = []
    for i in range(weeks - 1, -1, -1):
        wk_end = now - i * WEEK
        wk_start = wk_end - WEEK
        n = 0
        for ad in ads:
            if not ad.start_date:
                continue
            start = float(ad.start_date)
            end = _effective_end(ad, now)
            if end is None:
                continue
            if start <= wk_end and end >= wk_start:
                n += 1
        series.append(n)
    return series


def new_ads_per_week(ads, *, weeks: int = 16, now: float | None = None) -> list[int]:
    """Count of ads that STARTED in each of the last `weeks` weeks (fresh-creative cadence)."""
    now = now if now is not None else _time.time()
    series = []
    for i in range(weeks - 1, -1, -1):
        wk_end = now - i * WEEK
        wk_start = wk_end - WEEK
        series.append(sum(1 for ad in ads
                          if ad.start_date and wk_start <= float(ad.start_date) < wk_end))
    return series


def wow_metrics(series: list[int]) -> dict:
    """Velocity metrics from a weekly active-ad-count series (oldest -> newest)."""
    if not series:
        return {"current": 0, "prev": 0, "wow_pct": None, "growth_4w": None,
                "peak": 0, "weeks_growing": 0, "trend": "flat"}
    current = series[-1]
    prev = series[-2] if len(series) >= 2 else 0
    wow_pct = round((current - prev) / prev * 100, 1) if prev else None
    base_4w = series[-5] if len(series) >= 5 else (series[0] if series else 0)
    growth_4w = round(current / base_4w, 2) if base_4w else None
    # consecutive most-recent weeks of non-decreasing count
    weeks_growing = 0
    for j in range(len(series) - 1, 0, -1):
        if series[j] >= series[j - 1] and series[j] > 0:
            weeks_growing += 1
        else:
            break
    if growth_4w is not None and growth_4w >= 1.3:
        trend = "scaling"
    elif growth_4w is not None and growth_4w <= 0.7:
        trend = "declining"
    else:
        trend = "steady"
    return {"current": current, "prev": prev, "wow_pct": wow_pct, "growth_4w": growth_4w,
            "peak": max(series), "weeks_growing": weeks_growing, "trend": trend}
