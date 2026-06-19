"""Validate the category-frame breadth lever BEFORE designing it (lesson: verify the
mechanism — release-mint looked fine and was empty).

Two questions:
  PART 1 (zero Meta): do per-CATEGORY App Store new-releases contain mineable head terms?
                      (proves the minting SOURCE has signal — self-renewing seed)
  PART 2 (bounded Meta): does sweeping under-covered-category terms surface NEW emerging
                      advertisers the current 6-basin flywheel misses? (proves the payoff)

NEW = page_id not already in discovery.candidates ∪ public.advertisers.
Read-only: never writes the DB.

Usage: AGF_DATABASE_URL=... AGF_PROXIES=... PYTHONPATH=src python scripts/probe_category_frame.py
"""
import asyncio
import re
from collections import Counter

import httpx
from sqlalchemy import text

from agf.db.engine import get_engine
from agf.discovery.sweep import classify_candidate
from agf.enrich_full.appstore import fetch_itunes_new_releases
from agf.extract.facade import Extractor

# Under-covered Apple non-game categories (the doc's §3 "under-mined" set) -> (genre_id,
# a canonical autocomplete-style seed term the category-frame would mint).
UNDER_COVERED = [
    ("Photo & Video", "6008", "ai avatar"),
    ("Food & Drink",  "6023", "meal planner"),
    ("Travel",        "6003", "travel itinerary"),
    ("Music",         "6011", "learn piano"),
    ("Entertainment", "6016", "manga reader"),
    ("Lifestyle",     "6012", "plant identifier"),
]

_STOP = set("the a an my your app apps free pro plus ai for to and of with your you "
            "best new get make maker me it is on in by his her smart go il la le el "
            "2024 2025 2026 mobile io".split())


def head_terms(names, k=6):
    """Naive recurring head terms from a category's new-release NAMES: token bigrams
    minus stopwords, ranked by frequency. Stand-in for the real miner (proves signal)."""
    bigrams = Counter()
    unigrams = Counter()
    for n in names:
        toks = [t for t in re.findall(r"[a-z]+", n.lower()) if t not in _STOP and len(t) > 2]
        unigrams.update(toks)
        for a, b in zip(toks, toks[1:]):
            bigrams[f"{a} {b}"] += 1
    top_bi = [w for w, c in bigrams.most_common(k) if c >= 2]
    top_uni = [w for w, c in unigrams.most_common(k) if c >= 3]
    return top_bi, top_uni


def main():
    eng = get_engine()
    client = httpx.Client(timeout=20, follow_redirects=True)
    with eng.connect() as conn:
        known = {r[0] for r in conn.execute(text(
            "SELECT page_id::text FROM discovery.candidates "
            "UNION SELECT page_id::text FROM public.advertisers")).all()}
    print(f"known page_ids in DB: {len(known)}\n")

    print("===== PART 1: per-category new-release head terms (zero Meta) =====")
    for cat, gid, _seed in UNDER_COVERED:
        rows = fetch_itunes_new_releases(client, country="us", genre=gid, limit=60)
        names = [r["name"] for r in rows]
        bi, uni = head_terms(names)
        print(f"\n[{cat}] {len(names)} new releases")
        print(f"   sample names: {names[:6]}")
        print(f"   recurring bigrams: {bi}")
        print(f"   recurring unigrams: {uni}")

    print("\n\n===== PART 2: sweep under-covered-category seed terms (bounded Meta) =====")
    ext = Extractor()
    grand_new = 0
    for cat, _gid, seed in UNDER_COVERED:
        try:
            ads = asyncio.run(ext.search(query=seed, countries=["US"],
                                         search_type="KEYWORD_UNORDERED", max_pages=2))
        except Exception as exc:
            print(f"[{cat:<14} '{seed}'] search ERROR: {exc!r}")
            continue
        disp = Counter()
        new_pids, seen_pids = set(), set()
        for ad in ads:
            d, row = classify_candidate(ad, keyword=seed, technique="cat_frame")
            disp[d] += 1
            if d == "candidate" and row is not None:
                pid = str(row["page_id"])
                (new_pids if pid not in known else seen_pids).add(pid)
        grand_new += len(new_pids)
        print(f"[{cat:<14} '{seed:<18}'] ads={len(ads):>3} "
              f"candidates={disp.get('candidate',0):>2} "
              f"NEW={len(new_pids):>2} already_known={len(seen_pids):>2} "
              f"drops={{k:v for k,v in disp.items() if k!='candidate'}}".replace(
                  "{k:v for k,v in disp.items() if k!='candidate'}",
                  str({k: v for k, v in disp.items() if k != 'candidate'})))
    print(f"\nTOTAL NEW advertisers surfaced across {len(UNDER_COVERED)} category terms: {grand_new}")
    client.close()


if __name__ == "__main__":
    main()
