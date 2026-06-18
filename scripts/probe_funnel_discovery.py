# scripts/probe_funnel_discovery.py
"""Diagnostic: is funnel discovery saturated by a tiny keyword net, or is there an
untapped long tail we're not reaching? For a few keywords, search Meta DEEP and report
per keyword: total ads, distinct advertisers, how many are already known (in
public.advertisers), how many resolve to an app store (the dropped 'app' bucket),
how many are web funnels, and whether deeper pages add NEW advertisers.

Run (from repo root, sandbox OFF):
    AGF_PROXIES='...' AGF_DATABASE_URL=postgresql://agf:agf@localhost:5433/agf \
    PYTHONPATH=src .venv/bin/python scripts/probe_funnel_discovery.py
"""
import asyncio
import os
from collections import defaultdict

from sqlalchemy import text

from agf.classify import classify_url
from agf.db.engine import get_engine
from agf.discovery import funnel_classify as fc
from agf.extract.facade import Extractor

FUNNEL_PHRASES = [("take the quiz", "KEYWORD_EXACT_PHRASE"),
                  ("personalized plan", "KEYWORD_EXACT_PHRASE"),
                  ("based on your answers", "KEYWORD_EXACT_PHRASE")]
NICHE = [("calorie counter app", "KEYWORD_UNORDERED")]
MAX_PAGES = 6


def _known_page_ids(engine, page_ids):
    if not page_ids:
        return set()
    with engine.connect() as c:
        rows = c.execute(text("SELECT page_id FROM public.advertisers WHERE page_id = ANY(:p)"),
                         {"p": list(page_ids)}).scalars().all()
    return set(rows)


def _disposition(ad):
    if not ad.page_id or not ad.link_url:
        return "no_link"
    if fc.host_of(ad.link_url) in fc.SOCIAL_HOSTS:
        return "social"
    if classify_url(ad.link_url).kind in ("ios", "android"):
        return "app"          # app-backed funnel — currently DROPPED by funnel_sweep
    return "web"


async def probe_one(extractor, keyword, search_type):
    # paginate one page at a time so we can see when NEW advertisers stop appearing
    seen_pages, per_page_new = set(), []
    all_ads = []
    for p in range(1, MAX_PAGES + 1):
        ads = await extractor.search(query=keyword, countries=["US"],
                                     search_type=search_type, max_pages=p)
        # extractor.search(max_pages=p) returns the first p pages cumulatively; diff to get page p
        new_ids = {a.page_id for a in ads if a.page_id} - seen_pages
        per_page_new.append(len(new_ids))
        seen_pages |= new_ids
        all_ads = ads
        if len(ads) and p > 1 and len(new_ids) == 0:
            break          # pagination exhausted (no new advertisers on this page)
    return all_ads, per_page_new


async def main():
    engine = get_engine()
    extractor = Extractor(search_delay=2.0, search_jitter=1.5)
    print(f"{'keyword':32s} {'ads':>4s} {'advs':>4s} {'known':>5s} {'new':>4s} "
          f"{'app':>4s} {'web':>4s} {'soc':>4s} per-page-new")
    for keyword, st in FUNNEL_PHRASES + NICHE:
        ads, per_page_new = await probe_one(extractor, keyword, st)
        disp = defaultdict(int)
        page_ids = set()
        for ad in ads:
            disp[_disposition(ad)] += 1
            if ad.page_id:
                page_ids.add(ad.page_id)
        known = _known_page_ids(engine, page_ids)
        new = page_ids - known
        print(f"{keyword[:32]:32s} {len(ads):>4d} {len(page_ids):>4d} {len(known):>5d} "
              f"{len(new):>4d} {disp['app']:>4d} {disp['web']:>4d} {disp['social']:>4d} "
              f"{per_page_new}")


if __name__ == "__main__":
    asyncio.run(main())
