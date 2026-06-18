"""Discover emerging advertisers from App Store new-releases via Meta typeahead.

The hand-written taxonomy saturates and the ad-copy flywheel can only DEEPEN basins it
already found (topical locality). App Store new-releases are emerging by construction
(userRatingCount=0 on launch) and span every store category, so they are a fresh, external
discovery surface. We can't keyword-mine them (live-tested: names are too sparse, store
descriptions are boilerplate prose), so instead we look each new app up in Meta Ad Library
*typeahead* by name: the apps that actually buy Meta ads surface as advertiser pages
(page_id + like-count), which we resolve to candidates through the SAME path the keyword
sweep uses (classify_candidate -> upsert_sweep_candidate -> triage).

Budget discipline: skip pages we already know (focus spend on novelty), veto incumbents by
typeahead like-count BEFORE scraping their listing, and cap listings scraped per run."""
import asyncio

from sqlalchemy import text

from agf.db.candidates import upsert_sweep_candidate
from agf.db.engine import begin
from agf.discovery.sweep import classify_candidate
from agf.enrich_full.appstore import fetch_itunes_new_releases, gplay_similar
from agf.scoring.vetoes import INCUMBENT_PAGE_LIKES_MAX

# Scrape EVERY App Store category, not just the ones the user has liked — a hidden gem
# is by definition in a category you aren't already looking at, so preference must filter
# the FEED (the p_like model), never the scrape gate. The only exclusions are structural,
# not preferential: Games are out of product scope (already vetoed via is_game, so scraping
# them just burns budget on rows triage will drop), and Stickers aren't standalone apps.
# Everything else consumer is in. Emerging-only is still enforced by the incumbent veto.
OUT_OF_SCOPE_GENRES = frozenset({"Games", "Stickers"})


def _known_page_ids(conn) -> set:
    """Every page_id already discovered (candidate or promoted advertiser) — pages we
    needn't re-scrape, so the run spends its budget on genuinely new advertisers."""
    return {r[0] for r in conn.execute(text(
        "SELECT page_id::text FROM discovery.candidates "
        "UNION SELECT page_id::text FROM public.advertisers")).all()}


def app_graph_seed_apps(engine, *, runner=None, max_seeds=20) -> list[dict]:
    """Seed apps from the app-graph of recently-kept Android apps: each one's Google Play
    'similar apps'. A winning app's neighbours jump vocabulary basins keyword search can't
    reach — feed these to expand_releases_via_typeahead's `extra_seeds`. Returns
    [{store_id, name, genre}] (deduped). Zero Meta traffic (Play sidecar only); `runner`
    is injectable for tests."""
    with engine.connect() as conn:
        pkgs = [r[0] for r in conn.execute(text(
            "SELECT store_id FROM discovery.candidates "
            "WHERE store = 'android' AND status = 'kept' "
            "ORDER BY last_seen_at DESC LIMIT :n"), {"n": max_seeds}).all()]
    out, seen = [], set()
    for pkg in pkgs:
        for app in gplay_similar(pkg, runner=runner):
            sid = app.get("store_id")
            if not sid or sid in seen:
                continue
            seen.add(sid)
            out.append(app)
    return out


def expand_releases_via_typeahead(engine, extractor, client, *,
                                  exclude_genres=OUT_OF_SCOPE_GENRES,
                                  country="us", limit=200, listing_pages=2,
                                  max_listings=120, like_ceiling=INCUMBENT_PAGE_LIKES_MAX,
                                  extra_seeds=(), fetch=fetch_itunes_new_releases) -> dict:
    """One pass: gather seed apps (new releases across ALL categories, plus any
    `extra_seeds` — e.g. app-graph neighbours), typeahead each name, resolve the new
    advertiser pages to candidates. `fetch` is injectable for tests; `extractor` provides
    async `typeahead` and `search_listing`. Returns stats. Bounds Meta spend via `max_listings`
    (page-listing scrapes per run) and by skipping already-known pages and typeahead-incumbents."""
    seeds = list(fetch(client, country=country, genre=None, limit=limit)) + list(extra_seeds)
    apps, seen_store = [], set()
    for a in seeds:                                   # dedup by store_id, drop out-of-scope
        sid = a.get("store_id")
        if a.get("genre") in exclude_genres or (sid and sid in seen_store):
            continue
        if sid:
            seen_store.add(sid)
        apps.append(a)
    with engine.connect() as conn:
        skip = _known_page_ids(conn)
    stats = {"apps": len(apps), "typeahead_pages": 0, "incumbent_skipped": 0,
             "scraped_pages": 0, "candidates_new": 0, "candidates_seen": 0, "errors": 0}
    for app in apps:
        if stats["scraped_pages"] >= max_listings:
            break
        try:
            pages = asyncio.run(extractor.typeahead(query=app["name"],
                                                    country=country.upper()))
        except Exception:                        # one name's typeahead must not kill the run
            stats["errors"] += 1
            continue
        for p in pages:
            pid = p.get("page_id")
            if not pid or pid in skip:
                continue
            skip.add(pid)                         # dedupe within and across apps this run
            stats["typeahead_pages"] += 1
            likes = p.get("page_like_count")
            if likes is not None and likes > like_ceiling:
                stats["incumbent_skipped"] += 1   # incumbent — don't scrape its listing
                continue
            if stats["scraped_pages"] >= max_listings:
                break
            try:
                records, _ = asyncio.run(
                    extractor.search_listing(page_id=pid, max_pages=listing_pages))
            except Exception:
                stats["errors"] += 1
                continue
            stats["scraped_pages"] += 1
            rows, seen_keys = [], set()
            for ad in records:
                disp, row = classify_candidate(ad, keyword=app["name"],
                                               technique="release_typeahead")
                if disp != "candidate" or row is None:
                    continue
                key = (row["page_id"], row["store"], row["store_id"])
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                rows.append(row)
            if rows:
                with begin(engine) as conn:
                    for row in rows:
                        _, was_new = upsert_sweep_candidate(conn, row)
                        stats["candidates_new" if was_new else "candidates_seen"] += 1
    return stats
