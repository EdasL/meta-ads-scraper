"""One-off diagnostic: WHY does release-expand resolve pages but yield 0 candidates?

Runs the real release-mint pipeline for a handful of App Store new-release apps and
prints, per app: the typeahead page matches (to judge name-match quality) and, for each
scraped listing, the per-ad destination + classify_url kind + classify_candidate
disposition. Bounded Meta traffic (MAX_SCRAPES listings). Read-only: never writes the DB.

Usage: AGF_DATABASE_URL=... AGF_PROXIES=... PYTHONPATH=src python scripts/probe_release_mint.py
"""
import asyncio
import os
from collections import Counter

import httpx

from agf.classify import classify_url
from agf.db.engine import get_engine
from agf.discovery.release_mint import OUT_OF_SCOPE_GENRES, _known_page_ids
from agf.discovery.sweep import classify_candidate
from agf.enrich_full.appstore import fetch_itunes_new_releases
from agf.extract.facade import Extractor
from agf.scoring.vetoes import INCUMBENT_PAGE_LIKES_MAX

MAX_APPS = int(os.environ.get("PROBE_MAX_APPS", "8"))
MAX_SCRAPES = int(os.environ.get("PROBE_MAX_SCRAPES", "6"))


def main():
    eng = get_engine()
    ext = Extractor()
    client = httpx.Client(timeout=20, follow_redirects=True)
    seeds = fetch_itunes_new_releases(client, country="us", genre=None, limit=60)
    seeds = [s for s in seeds if s.get("genre") not in OUT_OF_SCOPE_GENRES][:MAX_APPS]
    with eng.connect() as conn:
        known = _known_page_ids(conn)
    print(f"seeds={len(seeds)} known_page_ids={len(known)} "
          f"(scrape budget={MAX_SCRAPES})\n")

    disp_total = Counter()
    kind_total = Counter()
    scrapes = 0
    for app in seeds:
        name = app["name"]
        try:
            pages = asyncio.run(ext.typeahead(query=name, country="US"))
        except Exception as exc:
            print(f"[{name}] typeahead ERROR: {exc!r}")
            continue
        fresh = [p for p in pages if p.get("page_id") and p["page_id"] not in known]
        print(f"[{name}] typeahead -> {len(pages)} pages, {len(fresh)} fresh; "
              f"matches: {[ (p.get('page_name'), p.get('page_like_count')) for p in pages[:5] ]}")
        for p in fresh:
            if scrapes >= MAX_SCRAPES:
                break
            likes = p.get("page_like_count")
            if likes is not None and likes > INCUMBENT_PAGE_LIKES_MAX:
                print(f"    - SKIP incumbent {p.get('page_name')} likes={likes}")
                continue
            pid = p["page_id"]
            try:
                records, _ = asyncio.run(ext.search_listing(page_id=pid, max_pages=2))
            except Exception as exc:
                print(f"    - {p.get('page_name')} search_listing ERROR: {exc!r}")
                continue
            scrapes += 1
            disp_c, samples = Counter(), []
            for ad in records:
                disp, _ = classify_candidate(ad, keyword=name, technique="probe")
                disp_c[disp] += 1
                kind_total[classify_url(ad.link_url).kind] += 1
                if len(samples) < 4:
                    samples.append((disp, classify_url(ad.link_url).kind, ad.link_url))
            disp_total.update(disp_c)
            print(f"    - page='{p.get('page_name')}' ads={len(records)} disp={dict(disp_c)}")
            for d, k, u in samples:
                print(f"        {d:<14} kind={k:<7} {str(u)[:90]}")
        if scrapes >= MAX_SCRAPES:
            break

    print("\n==== TOTALS ====")
    print("disposition:", dict(disp_total))
    print("classify_url kind:", dict(kind_total))
    client.close()


if __name__ == "__main__":
    main()
