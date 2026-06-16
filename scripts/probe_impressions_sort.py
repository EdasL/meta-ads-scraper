"""Probe: does Meta honor SORT_BY_TOTAL_IMPRESSIONS for a PER-PAGE (view_all_page_id)
ad fetch — the way we'd scrape one advertiser's ads?

We know the impressions sort works for KEYWORD search (the discovery "impressions
firehose" surfaced the biggest spenders). But server-side `startDate` is silently
IGNORED, so we must not assume `sortData` is honored for page-scoped queries — we
have to prove it before building rank-capture + a corpus re-scrape on top of it.

Method, per advertiser (US, ALL status, same path as search_listing):
  - fetch the listing three ways: default (no sortData), DESCENDING, ASCENDING
  - if DESC and ASC return the SAME ad order, the sort is IGNORED (the verdict that
    kills the plan). If they differ, the sort is HONORED — and we check whether DESC
    front-loads longer-running ads (run-days from start/end), the signal we want.

Run:  source ops/.env && .venv/bin/python scripts/probe_impressions_sort.py [N_ADVERTISERS] [MAX_PAGES]
(AGF_PROXIES + AGF_DATABASE_URL must be in env.)
"""
import asyncio
import os
import sys
import time

from sqlalchemy import text

from agf.db.engine import get_engine
from agf.extract.facade import Extractor

N_ADV = int(sys.argv[1]) if len(sys.argv) > 1 else 5
MAX_PAGES = int(sys.argv[2]) if len(sys.argv) > 2 else 2
# Country filter to test the sort under. EU countries (DE/GB/FR) publish per-ad reach
# under transparency rules, so the impressions sort may only bite there — not for US.
COUNTRIES = (sys.argv[3].split(",") if len(sys.argv) > 3 else ["US"])


def pick_advertisers(engine, n):
    """Advertisers with a page_id and enough stored ads that ordering is meaningful."""
    with engine.connect() as c:
        rows = c.execute(text(
            """
            SELECT a.id, a.page_id, a.page_name, count(ad.id) AS n_ads
            FROM public.advertisers a
            JOIN public.ads ad ON ad.advertiser_id = a.id
            WHERE a.page_id IS NOT NULL
            GROUP BY a.id, a.page_id, a.page_name
            HAVING count(ad.id) >= 8
            ORDER BY count(ad.id) DESC
            LIMIT :n
            """
        ), {"n": n}).all()
    return [(r[0], str(r[1]), r[2], r[3]) for r in rows]


def order_ids(records):
    """De-duped ad_archive_id order as returned (first occurrence wins)."""
    seen, out = set(), []
    for r in records:
        i = r.ad_archive_id
        if i and i not in seen:
            seen.add(i)
            out.append(i)
    return out


def run_days(r, now):
    if not r.start_date:
        return None
    end = r.end_date if (r.end_date and not r.is_active) else now
    return max(0, int((end - r.start_date) // 86400))


def days_by_id(records, now):
    d = {}
    for r in records:
        if r.ad_archive_id not in d:
            d[r.ad_archive_id] = run_days(r, now)
    return d


def prefix_overlap(a, b, k=5):
    """How many of the top-k match (same id, ignoring position) — a coarse 'same front?'"""
    return len(set(a[:k]) & set(b[:k]))


async def main():
    db = os.environ.get("AGF_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not db:
        sys.exit("no AGF_DATABASE_URL / DATABASE_URL in env (source ops/.env)")
    engine = get_engine(db)
    advs = pick_advertisers(engine, N_ADV)
    if not advs:
        sys.exit("no advertisers with page_id and >=8 ads")

    ex = Extractor()
    now = int(time.time())
    print(f"probing {len(advs)} advertisers, max_pages={MAX_PAGES}, US/ALL\n")
    honored = ignored = 0
    for adv_id, pid, name, n_ads in advs:
        try:
            default = await ex.search(query="", countries=COUNTRIES, active_status="ALL",
                                      search_type="page", view_all_page_id=pid,
                                      sort_mode=None, max_pages=MAX_PAGES)
            desc = await ex.search(query="", countries=COUNTRIES, active_status="ALL",
                                   search_type="page", view_all_page_id=pid,
                                   sort_mode="DESCENDING", max_pages=MAX_PAGES)
            asc = await ex.search(query="", countries=COUNTRIES, active_status="ALL",
                                  search_type="page", view_all_page_id=pid,
                                  sort_mode="ASCENDING", max_pages=MAX_PAGES)
        except Exception as exc:
            print(f"  adv {adv_id} ({name!r:.30}): ERROR {type(exc).__name__}: {exc}")
            continue

        o_def, o_desc, o_asc = order_ids(default), order_ids(desc), order_ids(asc)
        same = o_desc == o_asc
        verdict = "IGNORED" if same else "HONORED"
        honored += int(not same)
        ignored += int(same)

        # For HONORED: is DESC roughly sorted by run-days descending? (the signal we want)
        days = days_by_id(desc, now)
        desc_days = [d for d in (days.get(i) for i in o_desc) if d is not None]
        monotone = sum(1 for x, y in zip(desc_days, desc_days[1:]) if x >= y)
        mono_frac = (monotone / (len(desc_days) - 1)) if len(desc_days) > 1 else 0.0

        print(f"  adv {adv_id:>6} {str(name)[:24]:24s} ads≈{n_ads:>3} | "
              f"got def/desc/asc = {len(o_def)}/{len(o_desc)}/{len(o_asc)} | "
              f"DESC==ASC? {'yes' if same else 'NO '} -> {verdict} | "
              f"desc front5∩def={prefix_overlap(o_desc, o_def)} | "
              f"desc run-days non-increasing={mono_frac:.0%}")
        if not same:
            print(f"        DESC top5 run-days: {desc_days[:5]}   ASC top5 run-days: "
                  f"{[days_by_id(asc, now).get(i) for i in o_asc][:5]}")

    print(f"\nTOTAL  honored={honored}  ignored={ignored}  of {len(advs)}")
    if honored and not ignored:
        print("VERDICT: HONORED for per-page fetches — safe to build rank-capture + re-scrape.")
    elif ignored and not honored:
        print("VERDICT: IGNORED — sortData is dropped for page queries (like startDate). "
              "Do NOT build; fall back to active->days-running ordering.")
    else:
        print("VERDICT: MIXED — inspect per-advertiser rows before building.")


if __name__ == "__main__":
    asyncio.run(main())
