"""Layer-0 external-frame keyword minting. Replaces the old release_mint page-matching
mechanism (dead — see docs/research/2026-07-02-novel-discovery-algorithm.md §5b).

Two jobs, both taste-blind:
  1. ensure_category_frame — every in-scope category always holds a floor of live
     keywords (taxonomy seeds), the fixed anti-collapse backbone.
  2. mint_keywords_from_releases — mint head-term SEARCH keywords from App Store
     new-release listings, biased toward under-covered categories.
Minted keywords are dynamic_keywords trials (kind='app'); the existing flywheel sweep
discovers/promotes/retires them. Zero Meta traffic here (Apple RSS + Lookup only)."""
from collections import defaultdict

from agf.db.dynamic_keywords import add_trial_keywords, tried_keywords
from agf.discovery.coverage import (canonical_category, category_coverage,
                                    mint_budget, under_floor_categories)
from agf.discovery.release_keywords import ProposeError, propose_release_keywords
from agf.discovery.taxonomy import NICHE_TERMS_BY_CATEGORY
from agf.enrich_full.appstore import fetch_itunes_new_releases, lookup_itunes_batch


def ensure_category_frame(conn, coverage, *, floor: int) -> int:
    """Seed taxonomy keywords for every under-floor in-scope category so no category can
    silently fall out of the sweep. Idempotent (existing keywords no-op). Returns added."""
    added = 0
    for cat in under_floor_categories(coverage, floor=floor):
        seeds = NICHE_TERMS_BY_CATEGORY.get(cat)
        if not seeds:
            continue
        added += add_trial_keywords(conn, seeds, source=f"frame:{cat}", kind="app")
    return added


def mint_keywords_from_releases(engine, client, *, country="us", limit=200, K=15,
                                floor=5, total_slots=40, min_advertisers=2,
                                explore_share=0.25,
                                fetch=fetch_itunes_new_releases,
                                lookup=lookup_itunes_batch,
                                propose=propose_release_keywords) -> dict:
    """Mint niche SEARCH keywords from App Store new-release listings, biased toward
    categories with recent keeper yield (exploit lane) plus an `explore_share` lane for
    under-covered categories, and keep the category frame above its floor. Taste-blind
    (yield = triage-kept counts, never ratings). Only categories with listing material
    this run receive slots, so none evaporate. Keywords are proposed by a cheap Gemini
    model per category (crisp 'X app' niche terms, not n-grams of store prose). Network:
    Apple RSS + Lookup (zero Meta) + Gemini text. Returns stats; never raises on a
    fetch/lookup/propose failure. `min_advertisers` is retained for signature stability
    but no longer used by the LLM proposer."""
    stats = {"seeds": 0, "in_scope": 0, "looked_up": 0, "minted": 0,
             "minted_by_category": {}, "frame_seeded": 0, "coverage": {}, "errors": 0}

    try:
        seeds = list(fetch(client, country=country, genre=None, limit=limit))
    except Exception:
        stats["errors"] += 1
        seeds = []
    stats["seeds"] = len(seeds)

    # In-scope only, dedup by store_id (iOS ids for the Apple Lookup).
    ids, seen = [], set()
    for a in seeds:
        sid = a.get("store_id")
        if not sid or sid in seen or canonical_category(a.get("genre")) is None:
            continue
        seen.add(sid)
        ids.append(sid)
    stats["in_scope"] = len(ids)

    metas = {}
    if ids:
        try:
            metas = lookup(client, ids, country=country)
        except Exception:
            stats["errors"] += 1
            metas = {}
    stats["looked_up"] = len(metas)

    # Group listings ({name, description}) by canonical category from the Lookup genre.
    by_cat: dict[str, list] = defaultdict(list)
    for m in metas.values():
        cat = canonical_category(getattr(m, "genre", None))
        if cat is None:
            continue
        by_cat[cat].append({"name": m.name, "description": m.description})

    with engine.begin() as conn:
        coverage = category_coverage(conn)
        budget = mint_budget(coverage, K=K, total_slots=total_slots,
                             explore_share=explore_share, eligible=set(by_cat))
        exclude = tried_keywords(conn, kind="app")
        for cat, slots in budget.items():
            listings = by_cat.get(cat)
            if not listings:
                continue
            try:
                proposed = propose(cat, listings, slots)
            except ProposeError:
                stats["errors"] += 1
                continue
            terms = [t for t in proposed if t.lower() not in exclude]  # dedup vs tried
            if not terms:
                continue
            added = add_trial_keywords(conn, terms, source=f"new_release:{cat}",
                                       kind="app")
            stats["minted"] += added
            stats["minted_by_category"][cat] = added
            exclude.update(t.lower() for t in terms)  # don't re-mint within this run
        stats["frame_seeded"] = ensure_category_frame(conn, coverage, floor=floor)
        stats["coverage"] = {c: {"found": cov.found, "live_keywords": cov.live_keywords}
                             for c, cov in coverage.items()}
    return stats
