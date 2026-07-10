"""What should the Ad Library keyword BASE be? Mine real Meta app-advertiser ad copy
(the user's idea) over the BROAD corpus (all advertisers, not just gem>=40), then sweep a
sample of the novel terms through the Ad Library to measure new-advertiser yield.

Read-only; bounded Meta (SWEEP_N terms). Usage:
  AGF_DATABASE_URL=... AGF_PROXIES=... PYTHONPATH=src python scripts/probe_mine_base.py
"""
import asyncio
import os
from collections import defaultdict

from sqlalchemy import text

from agf.db.engine import get_engine
from agf.db.dynamic_keywords import tried_keywords
from agf.discovery.keyword_mining import mine_phrases
from agf.discovery.sweep import classify_candidate
from agf.discovery.taxonomy import all_entries
from agf.extract.facade import Extractor

SWEEP_N = int(os.environ.get("SWEEP_N", "6"))


def main():
    eng = get_engine()
    with eng.connect() as conn:
        rows = conn.execute(text(
            "SELECT a.advertiser_id, COALESCE(ap.genre,'(none)') genre, c.body_text "
            "FROM public.ads a JOIN public.creatives c ON c.ad_id=a.id "
            "LEFT JOIN public.apps ap ON ap.id=a.app_id "
            "WHERE NULLIF(c.body_text,'') IS NOT NULL")).all()
        exclude = {e.keyword for e in all_entries()} | tried_keywords(conn) \
            | tried_keywords(conn, kind="funnel")
        known_pids = {r[0] for r in conn.execute(text(
            "SELECT page_id::text FROM discovery.candidates "
            "UNION SELECT page_id::text FROM public.advertisers")).all()}

    broad = [(r[0], r[2]) for r in rows]
    by_cat = defaultdict(list)
    for r in rows:
        by_cat[r[1]].append((r[0], r[2]))
    print(f"broad corpus: {len(broad)} creatives across {len(by_cat)} genres; "
          f"exclude set={len(exclude)} known terms\n")

    print("===== GLOBAL: top novel mined phrases (whole corpus) =====")
    global_terms = mine_phrases(broad, exclude=exclude, min_advertisers=3, limit=30)
    for t in global_terms:
        print(f"   {t}")

    print("\n===== PER-CATEGORY: top novel mined phrases (under-covered first) =====")
    cats = sorted(by_cat, key=lambda k: len(by_cat[k]))
    picks = []
    for cat in cats:
        if cat == "(none)" or len(by_cat[cat]) < 6:
            continue
        terms = mine_phrases(by_cat[cat], exclude=exclude, min_advertisers=2, limit=6)
        if terms:
            print(f"[{cat}] ({len(by_cat[cat])} creatives): {terms}")
            picks.extend(terms[:1])

    # sweep a spread: a few global + a few per-category novel terms
    sample = []
    for t in (global_terms[:3] + picks):
        if t not in sample:
            sample.append(t)
    sample = sample[:SWEEP_N]
    print(f"\n===== SWEEP {len(sample)} novel mined terms through the Ad Library =====")
    ext = Extractor()
    total_new = 0
    for term in sample:
        try:
            ads = asyncio.run(ext.search(query=term, countries=["US"],
                                         search_type="KEYWORD_UNORDERED", max_pages=2))
        except Exception as exc:
            print(f"[{term!r}] ERROR {exc!r}")
            continue
        cand = newp = 0
        for ad in ads:
            d, row = classify_candidate(ad, keyword=term, technique="mine_base")
            if d == "candidate" and row is not None:
                cand += 1
                if str(row["page_id"]) not in known_pids:
                    newp += 1
        total_new += newp
        print(f"   {term!r:<34} ads={len(ads):>3} candidates={cand:>2} NEW={newp:>2}")
    print(f"\nTOTAL NEW advertisers from {len(sample)} mined terms: {total_new}")


if __name__ == "__main__":
    main()
