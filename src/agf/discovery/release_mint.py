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
from agf.enrich_full.appstore import fetch_itunes_new_releases
from agf.scoring.vetoes import INCUMBENT_PAGE_LIKES_MAX

# In-scope App Store category labels (as Apple's RSS returns them) — the consumer /
# self-improvement basins. Games, B2B (Business/Developer Tools), Stickers and system
# utilities are out of scope, so their new releases don't burn typeahead/scrape budget.
IN_SCOPE_GENRES = frozenset({
    "Health & Fitness", "Education", "Food & Drink", "Lifestyle", "Productivity",
    "Finance", "Photo & Video", "Music", "Travel", "Shopping", "Social Networking",
    "Reference", "Entertainment", "Medical", "Sports", "Books", "News", "Weather",
})


def _known_page_ids(conn) -> set:
    """Every page_id already discovered (candidate or promoted advertiser) — pages we
    needn't re-scrape, so the run spends its budget on genuinely new advertisers."""
    return {r[0] for r in conn.execute(text(
        "SELECT page_id::text FROM discovery.candidates "
        "UNION SELECT page_id::text FROM public.advertisers")).all()}


def expand_releases_via_typeahead(engine, extractor, client, *, genres=IN_SCOPE_GENRES,
                                  country="us", limit=200, listing_pages=2,
                                  max_listings=120, like_ceiling=INCUMBENT_PAGE_LIKES_MAX,
                                  fetch=fetch_itunes_new_releases) -> dict:
    """One pass: fetch new releases, typeahead each name, resolve the new advertiser pages
    to candidates. `fetch` is injectable for tests; `extractor` provides async `typeahead`
    and `search_listing`. Returns stats. Bounds Meta spend via `max_listings` (page-listing
    scrapes per run) and by skipping already-known pages and typeahead-incumbents."""
    apps = [a for a in fetch(client, country=country, genre=None, limit=limit)
            if a.get("genre") in genres]
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
