"""Keyword-seed technique bake-off — measure how well a candidate keyword seed
finds emerging apps and how DIVERSE (un-biased) its catch is.

RESEARCH HARNESS. Does NOT write to the production DB (unlike sweep_smoke). For
each keyword it runs a bounded live Meta Ad Library search, vetoes incumbents by
page_like_count, keeps app-store-linked advertisers (deduped by page_id),
resolves each app's store genre, and prints a scorecard comparable to the
niche_term baseline:

    top-genre share · top-3 share · category evenness (entropy/log2 n) ·
    app-candidates/keyword (yield) · dead-keyword rate

The anti-bias metrics (top-genre share, evenness) are the point: a balanced seed
should drive top-genre share DOWN and evenness UP vs. the 26% / 76% baseline.

Run (from repo root):
    AGF_PROXIES="http://user:pass@gw.dataimpulse.com:823" PYTHONPATH=src \
        .venv/bin/python scripts/keyword_seed_experiment.py <seed_file> <technique_label>

Env: AGF_EXP_PAGES (SERP pages/keyword, default 2), AGF_KW_DELAY (inter-kw pause,
default 3.0), AGF_VETO_LIKES (incumbent ceiling; default = production veto).
Writes /tmp/agf-kwexp-<technique>-<YYYY-MM-DD>.json for cross-iteration compare.
"""
import asyncio
import json
import math
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import date

import httpx

from agf.classify import classify_url
from agf.enrich_full.appstore import lookup_google_play, lookup_itunes_batch
from agf.extract.errors import ExtractError
from agf.extract.facade import Extractor

PAGES = int(os.environ.get("AGF_EXP_PAGES", "2"))
DELAY = float(os.environ.get("AGF_KW_DELAY", "3.0"))
# Experiment-only knob. The production pipeline no longer has a page-likes
# ceiling for apps (removed 2026-07-10); 10k kept here as the historical default.
VETO_LIKES = int(os.environ.get("AGF_VETO_LIKES", "10000"))
COUNTRIES = ["US"]


def _env_skip_genre():
    return os.environ.get("AGF_SKIP_GENRE", "").lower() in ("1", "true", "yes")


def load_keywords(path):
    with open(path) as f:
        return [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]


def sweep(ext, keywords):
    """Bounded live sweep. Returns (apps, per_kw_counts, raw_ads, errors, corpus).
    apps: {(store, store_id): {store, store_id, name, keyword, pages:set}} — deduped at
    the APP level (store_id), NOT page_id, so a prolific multi-page advertiser (e.g.
    PocketFM running 8 ad pages) counts as ONE app, not 8.
    corpus: [(page_id, body_text)] for every app-candidate ad — the flywheel mining
    input (page_id is the cross-advertiser author key mine_phrases counts on)."""
    apps, per_kw, raw, errors, corpus = {}, defaultdict(int), 0, 0, []
    for i, kw in enumerate(keywords):
        try:
            ads = asyncio.run(ext.search(query=kw, countries=COUNTRIES,
                                         search_type="KEYWORD_UNORDERED", max_pages=PAGES))
        except ExtractError as exc:
            print(f"  [{i+1}/{len(keywords)}] {kw!r} -> ERR {exc}", flush=True)
            errors += 1
            time.sleep(DELAY * 2)
            continue
        new_here = 0
        for ad in ads:
            raw += 1
            if not ad.page_id or not ad.link_url:
                continue
            likes = ad.page_like_count
            if likes is not None and likes > VETO_LIKES:
                continue
            intent = classify_url(ad.link_url)
            if intent.kind not in ("ios", "android") or not intent.store_id:
                continue
            key = (intent.kind, str(intent.store_id))
            if key not in apps:
                apps[key] = {"store": intent.kind, "store_id": str(intent.store_id),
                             "name": ad.page_name or "(unknown)", "keyword": kw,
                             "pages": {ad.page_id}}
                new_here += 1
            else:
                apps[key]["pages"].add(ad.page_id)
            if ad.body_text:                         # flywheel mining corpus
                corpus.append((ad.page_id, ad.body_text))
        per_kw[kw] += new_here
        print(f"  [{i+1}/{len(keywords)}] {kw!r}: {len(ads)} ads, +{new_here} new apps "
              f"(distinct apps {len(apps)})", flush=True)
        time.sleep(DELAY)
    return apps, per_kw, raw, errors, corpus


def resolve_genres(apps):
    """Resolve each distinct app's store genre. Returns {(store,store_id): genre}.
    Android ids are looked up in small chunks so the node sidecar never times out
    (the 90s single-call timeout silently dropped ALL android genres in an early run)."""
    ios = [a["store_id"] for a in apps.values() if a["store"] == "ios"]
    android = [a["store_id"] for a in apps.values() if a["store"] == "android"]
    meta = {}
    with httpx.Client(timeout=20, follow_redirects=True) as client:
        if ios:
            for tid, m in lookup_itunes_batch(client, ios, pause=3.0).items():
                meta[("ios", str(tid))] = getattr(m, "genre", None)
    # Small chunks: the node sidecar is ~6s/id, so 15 ids blow the 90s timeout
    # under load and silently drop genres. 5 ids (~30s) stays well under.
    for j in range(0, len(android), 5):
        chunk = android[j:j + 5]
        try:
            for pid, m in lookup_google_play(chunk).items():
                meta[("android", str(pid))] = getattr(m, "genre", None)
        except Exception as exc:           # one chunk failing must not drop the rest
            print(f"  (google-play chunk {j//5} skipped: {exc})", flush=True)
    return {k: meta.get(k) for k in apps}


def mine(corpus, seed_keywords):
    """Run the REAL flywheel miner over the seed's catch. Returns how many NEW
    promotable keywords it yields (phrases clearing the cross-advertiser bar that
    aren't already swept). exclude = this seed ∪ the existing static taxonomy ∪
    every dynamic keyword ever tried (so mined phrases are genuinely new)."""
    from agf.discovery.keyword_mining import mine_phrases
    from agf.discovery import taxonomy
    exclude = {k.lower() for k in seed_keywords}
    exclude |= {e.keyword.lower() for e in taxonomy.all_entries()}
    if os.environ.get("AGF_DATABASE_URL"):
        try:
            from agf.db.engine import get_engine
            from sqlalchemy import text
            with get_engine().connect() as c:
                exclude |= {r[0].lower() for r in c.execute(text(
                    "SELECT keyword FROM discovery.dynamic_keywords")).all()}
        except Exception as exc:
            print(f"  (dynamic_keywords exclude skipped: {exc})", flush=True)
    advertisers = len({pid for pid, _ in corpus})
    # limit high so we COUNT all promotable phrases (production caps per-run at 10).
    mined = mine_phrases(corpus, exclude=exclude, min_advertisers=2, limit=500)
    return {"corpus_ads": len(corpus), "corpus_advertisers": advertisers,
            "promotable_keywords": len(mined), "sample": mined[:40]}


def novelty(apps):
    """How many distinct apps are NOT already in the corpus (discovery.candidates ∪
    public.apps). Needs AGF_DATABASE_URL; returns None if unavailable."""
    if not os.environ.get("AGF_DATABASE_URL"):
        return None
    try:
        from agf.db.engine import get_engine
        from sqlalchemy import text
        with get_engine().connect() as c:
            known = {r[0] for r in c.execute(text(
                "SELECT store_id FROM discovery.candidates "
                "UNION SELECT store_id FROM public.apps"), ).all()}
        new = [a for a in apps.values() if a["store_id"] not in known]
        return {"distinct": len(apps), "new": len(new), "known": len(apps) - len(new),
                "new_pct": round(len(new) / max(1, len(apps)), 3)}
    except Exception as exc:
        print(f"  (novelty check skipped: {exc})", flush=True)
        return None


def scorecard(technique, keywords, apps, per_kw, raw, errors, genre_by_app, nov):
    genres = Counter(g for g in genre_by_app.values() if g)
    tot = sum(genres.values())
    H = -sum((n / tot) * math.log2(n / tot) for n in genres.values()) if tot else 0.0
    Hmax = math.log2(len(genres)) if len(genres) > 1 else 1.0
    dead = sum(1 for kw in keywords if per_kw.get(kw, 0) == 0)
    sc = {
        "technique": technique,
        "keywords": len(keywords),
        "raw_ads": raw,
        "errors": errors,
        "distinct_apps": len(apps),               # deduped by store_id (true app count)
        "genre_resolved": tot,
        "genre_coverage": round(tot / max(1, len(apps)), 3),   # <— trust gate
        "yield_per_kw": round(len(apps) / max(1, len(keywords)), 2),
        "dead_kw": dead,
        "dead_kw_pct": round(dead / max(1, len(keywords)), 3),
        "novelty": nov,                           # new vs existing corpus
        "n_genres": len(genres),
        "top_genre": genres.most_common(1)[0] if genres else None,
        "top_genre_share": round(genres.most_common(1)[0][1] / tot, 3) if tot else None,
        "top3_share": round(sum(n for _, n in genres.most_common(3)) / tot, 3) if tot else None,
        "evenness": round(H / Hmax, 3) if Hmax else None,
        "genre_hist": genres.most_common(),
        "apps": [{"store": a["store"], "store_id": a["store_id"], "name": a["name"],
                  "keyword": a["keyword"], "n_pages": len(a["pages"]),
                  "genre": genre_by_app.get(k)} for k, a in apps.items()],
    }
    return sc


def main():
    if len(sys.argv) < 3:
        sys.exit("usage: keyword_seed_experiment.py <seed_file> <technique_label>")
    if not os.environ.get("AGF_PROXIES"):
        sys.exit("AGF_PROXIES required (residential proxy).")
    seed_file, technique = sys.argv[1], sys.argv[2]
    keywords = load_keywords(seed_file)
    print(f"=== technique {technique!r}: {len(keywords)} keywords, {PAGES} pages each ===",
          flush=True)
    ext = Extractor()
    apps, per_kw, raw, errors, corpus = sweep(ext, keywords)
    # Genre resolution is slow + flaky (android sidecar timeouts). Diversity is
    # measured by SEED CATEGORY (100% coverage) anyway, so allow skipping it.
    genre_by_app = {} if _env_skip_genre() else resolve_genres(apps)
    nov = novelty(apps)
    sc = scorecard(technique, keywords, apps, per_kw, raw, errors, genre_by_app, nov)
    try:
        sc["flywheel"] = mine(corpus, keywords)
    except Exception as exc:               # never lose the sweep over a mining bug
        print(f"  (mining failed: {exc!r})", flush=True)
        sc["flywheel"] = {"error": repr(exc), "corpus_ads": len(corpus)}
    out = f"/tmp/agf-kwexp-{technique}-{date.today().isoformat()}.json"
    with open(out, "w") as f:
        json.dump(sc, f, indent=2)
    print("\n=== SCORECARD ===", flush=True)
    for k in ("technique", "keywords", "distinct_apps", "genre_coverage", "yield_per_kw",
              "dead_kw_pct", "novelty", "n_genres", "top_genre", "top_genre_share",
              "top3_share", "evenness"):
        print(f"  {k:18s} {sc[k]}", flush=True)
    print(f"  genre_hist: {sc['genre_hist']}", flush=True)
    fw = sc["flywheel"]
    print(f"\n=== FLYWHEEL ===", flush=True)
    print(f"  corpus: {fw['corpus_ads']} ads / {fw['corpus_advertisers']} advertisers",
          flush=True)
    print(f"  NEW promotable keywords: {fw['promotable_keywords']}", flush=True)
    print(f"  sample: {fw['sample']}", flush=True)
    print(f"\nwrote {out}", flush=True)


if __name__ == "__main__":
    main()
