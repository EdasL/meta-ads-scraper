"""Head-to-head: for App Store new-release apps, does feeding the app NAME as a normal
keyword search beat the typeahead-by-name path? Tests the user's proposal vs the prior
build's "names too sparse" claim. Read-only, bounded Meta traffic.

For each seed app it runs the NORMAL sweep search (KEYWORD_UNORDERED) on the app name and
reports: ads found, classify dispositions, # app candidates, and whether any candidate's
store_id == the SEED app's store_id (i.e. did we actually find THIS app).

Usage: AGF_DATABASE_URL=... AGF_PROXIES=... PYTHONPATH=src python scripts/probe_release_keyword_vs_typeahead.py
"""
import asyncio
import os
from collections import Counter

import httpx

from agf.discovery.release_mint import OUT_OF_SCOPE_GENRES
from agf.discovery.sweep import classify_candidate
from agf.enrich_full.appstore import fetch_itunes_new_releases
from agf.extract.facade import Extractor

MAX_APPS = int(os.environ.get("PROBE_MAX_APPS", "12"))
PAGES = int(os.environ.get("PROBE_PAGES", "2"))


def main():
    ext = Extractor()
    client = httpx.Client(timeout=20, follow_redirects=True)
    seeds = fetch_itunes_new_releases(client, country="us", genre=None, limit=80)
    seeds = [s for s in seeds if s.get("genre") not in OUT_OF_SCOPE_GENRES][:MAX_APPS]
    print(f"testing {len(seeds)} new-release apps via KEYWORD_UNORDERED search "
          f"(pages={PAGES})\n")

    disp_total = Counter()
    apps_with_any_candidate = 0
    apps_with_self_match = 0
    for app in seeds:
        name = app["name"]
        sid = str(app.get("store_id") or "")
        try:
            ads = asyncio.run(ext.search(query=name, countries=["US"],
                                         search_type="KEYWORD_UNORDERED", max_pages=PAGES))
        except Exception as exc:
            print(f"[{name}] search ERROR: {exc!r}")
            continue
        disp_c = Counter()
        cand_store_ids = set()
        for ad in ads:
            disp, row = classify_candidate(ad, keyword=name, technique="release_keyword")
            disp_c[disp] += 1
            if disp == "candidate" and row is not None:
                cand_store_ids.add(str(row["store_id"]))
        disp_total.update(disp_c)
        ncand = disp_c.get("candidate", 0)
        self_match = sid in cand_store_ids
        if ncand:
            apps_with_any_candidate += 1
        if self_match:
            apps_with_self_match += 1
        print(f"[{name[:34]:<34}] ads={len(ads):>3} candidates={ncand:>2} "
              f"self_match={'YES' if self_match else 'no':<3} disp={dict(disp_c)}")

    n = len(seeds)
    print(f"\n==== TOTALS over {n} apps ====")
    print("disposition:", dict(disp_total))
    print(f"apps yielding >=1 app candidate: {apps_with_any_candidate}/{n}")
    print(f"apps where a candidate IS the seed app (store_id match): {apps_with_self_match}/{n}")
    client.close()


if __name__ == "__main__":
    main()
