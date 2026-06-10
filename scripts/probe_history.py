"""Probe: can we reconstruct a page's week-over-week ad-count curve from ONE scrape?

The bet: Meta's Ad Library keeps INACTIVE ads with start_date AND end_date. If a page-scoped
pull with active_status=ALL returns the full history, we can backfill the weekly active-ad-count
(and thus velocity) retroactively instead of waiting weeks to accumulate snapshots.

Run: AGF_PROXIES=... PYTHONPATH=src .venv/bin/python scripts/probe_history.py <page_id> [page_id...]
"""
import sys
import time

from agf.extract.facade import Extractor

WEEKS = 16
DAY = 86400


def reconstruct_weekly(ads, now):
    """active_ad_count for each of the last WEEKS weeks, from per-ad start/end dates."""
    series = []
    for i in range(WEEKS - 1, -1, -1):
        wk_end = now - i * 7 * DAY
        wk_start = wk_end - 7 * DAY
        n = 0
        for a in ads:
            start = a.start_date
            if not start:
                continue
            end = a.end_date or (now if a.is_active else start)
            if start <= wk_end and end >= wk_start:
                n += 1
        series.append(n)
    return series


def probe_page(ext, page_id):
    print(f"\n===== page {page_id} =====")
    for status in ("ACTIVE", "ALL"):
        try:
            ads = ext.search(query="", countries=["US"], search_type="page",
                             view_all_page_id=page_id, active_status=status, max_pages=10)
            ads = _run(ads)
        except Exception as exc:  # noqa: BLE001
            print(f"  {status}: error {exc}")
            continue
        active = sum(1 for a in ads if a.is_active)
        with_end = sum(1 for a in ads if a.end_date)
        starts = [a.start_date for a in ads if a.start_date]
        if not ads:
            print(f"  {status}: 0 ads")
            continue
        oldest = round((time.time() - min(starts)) / DAY) if starts else None
        newest = round((time.time() - max(starts)) / DAY) if starts else None
        print(f"  {status}: total={len(ads)} active={active} inactive={len(ads) - active} "
              f"with_end_date={with_end} oldest_start={oldest}d newest_start={newest}d ago")
        if status == "ALL":
            series = reconstruct_weekly(ads, time.time())
            print(f"  reconstructed weekly active-ad-count (oldest->newest, {WEEKS}w): {series}")
            if series[-1] and series[max(0, WEEKS - 5)]:
                wow = series[-1] / series[max(0, WEEKS - 5)]
                print(f"  4-week ad-count change: x{wow:.2f}")


def _run(coro):
    import asyncio
    return asyncio.run(coro) if hasattr(coro, "__await__") else coro


def main():
    if len(sys.argv) < 2:
        print("usage: probe_history.py <page_id> [page_id...]")
        sys.exit(1)
    ext = Extractor(search_delay=2.0, search_jitter=1.5)
    for attempt in range(6):
        try:
            ext._pool.invalidate()
            ext._pool.acquire()
            break
        except Exception as exc:  # noqa: BLE001
            print(f"warm {attempt} failed: {exc}")
            time.sleep(2)
    for pid in sys.argv[1:]:
        probe_page(ext, pid)
        time.sleep(4)


if __name__ == "__main__":
    main()
