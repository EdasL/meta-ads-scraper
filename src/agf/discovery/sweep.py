"""Stage 1 of breadth-first discovery (spec 2026-06-10): a wide, shallow, US-only
keyword sweep that persists app-linked candidates using ONLY fields already present
on the SERP result — zero requests beyond the search pages themselves.

Inline drops (cheapest-first): no link -> social host -> incumbent page likes
(INCUMBENT_PAGE_LIKES_MAX, the user-set 10k constant — single source of truth in
scoring.vetoes) -> non-app destination (funnels are v2) -> store link without an id.

Commits per keyword (short transactions), so a mid-run throttle-kill keeps progress."""
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

from agf.classify import classify_url
from agf.db.candidates import upsert_sweep_candidate
from agf.db.engine import begin
from agf.discovery.taxonomy import sweep_entries
from agf.extract.errors import ErrorKind, ExtractError
from agf.scoring.vetoes import INCUMBENT_PAGE_LIKES_MAX

# Destinations that are neither an app nor a funnel — a social page or ad redirect.
SOCIAL_HOSTS = {"facebook.com", "fb.me", "m.facebook.com", "l.facebook.com", "lm.facebook.com",
                "instagram.com", "api.whatsapp.com", "wa.me", "t.co", "ad.doubleclick.net",
                "youtube.com", "youtu.be", "twitter.com", "x.com", "linktr.ee", "messenger.com"}


def _host(url: str | None) -> str:
    return urlparse(url).netloc.lower().replace("www.", "") if url else ""


def _start_date(epoch) -> object | None:
    if not epoch:
        return None
    try:
        return datetime.fromtimestamp(float(epoch), tz=timezone.utc).date()
    except (TypeError, ValueError, OSError):
        return None


def classify_candidate(ad, *, keyword: str, technique: str) -> tuple[str, dict | None]:
    """Fold one AdRecord into (disposition, candidate-row). Dispositions:
    'candidate' | 'no_link' | 'social' | 'incumbent_likes' | 'non_app' | 'no_store_id'."""
    if not ad.page_id or not ad.link_url:
        return "no_link", None
    if _host(ad.link_url) in SOCIAL_HOSTS:
        return "social", None
    likes = ad.page_like_count
    if likes is not None and likes > INCUMBENT_PAGE_LIKES_MAX:
        return "incumbent_likes", None
    intent = classify_url(ad.link_url)
    if intent.kind not in ("ios", "android"):
        return "non_app", None
    if not intent.store_id:
        return "no_store_id", None
    return "candidate", {
        "page_id": ad.page_id, "page_name": ad.page_name, "page_like_count": likes,
        "store": intent.kind, "store_id": str(intent.store_id), "link_url": ad.link_url,
        "cta_type": ad.cta_type, "ad_start_date": _start_date(ad.start_date),
        "source_keyword": keyword, "technique": technique,
    }


async def run_sweep(engine, extractor, *, entries=None, keywords=None,
                    countries=("US",), max_pages: int = 2, kw_delay: float = 3.0,
                    max_consecutive_errors: int = 0, sleep=time.sleep,
                    heartbeat=None) -> dict:
    """Sweep the taxonomy (or an explicit entry/keyword subset) and persist candidates.
    Returns counters for sweep_runs. `keywords` (list[str]) is a convenience override
    used by the worker dry-run; it implies KEYWORD_UNORDERED.

    `max_consecutive_errors` is the dead-proxy circuit-breaker: each keyword's search
    only raises after the facade exhausts its retries+IP-rotations, so N of them in a row
    means the proxy/gateway is down (or Meta is hard-throttling). Rather than grind all
    ~300 keywords at ~one timeout-cycle each (the 2026-06-19 wedge), the sweep RAISES so
    run_once fails this cycle and the loop retries next tick. 0 disables (the default, so
    the dry-run and the per-keyword flywheel sweeps are unaffected); the counter resets on
    any successful keyword, so isolated throttles never trip it."""
    if entries is None:
        entries = sweep_entries()
    if keywords is not None:
        entries = [e for e in entries if e.keyword in set(keywords)]
    stats = {"keywords_swept": 0, "serp_requests": 0, "raw_ads": 0,
             "candidates_new": 0, "candidates_seen": 0, "errors": 0,
             "drops": {"no_link": 0, "social": 0, "incumbent_likes": 0,
                       "non_app": 0, "no_store_id": 0}}
    consecutive_errors = 0
    for entry in entries:
        # entry.max_pages is the per-keyword ceiling (the experiment harness honors it
        # in full); the production sweep further caps every keyword at `max_pages`
        # (AGF_SWEEP_PAGES) so taxonomy edits can lower a keyword's cost but not raise it.
        pages = min(entry.max_pages, max_pages)
        try:
            ads = await extractor.search(query=entry.keyword, countries=list(countries),
                                         search_type=entry.search_type, max_pages=pages)
        except ExtractError:
            stats["errors"] += 1
            consecutive_errors += 1
            if max_consecutive_errors and consecutive_errors >= max_consecutive_errors:
                raise ExtractError(
                    f"sweep circuit-breaker: {consecutive_errors} consecutive search "
                    f"errors — proxy/gateway likely down, aborting cycle",
                    ErrorKind.TRANSIENT)
            sleep(kw_delay * 2)
            continue
        consecutive_errors = 0                        # any success clears the streak
        stats["keywords_swept"] += 1
        stats["serp_requests"] += pages              # upper bound; search may stop earlier
        stats["raw_ads"] += len(ads)
        rows, seen_keys = [], set()
        for ad in ads:
            disposition, row = classify_candidate(ad, keyword=entry.keyword,
                                                  technique=entry.layer)
            if disposition != "candidate":
                stats["drops"][disposition] += 1
                continue
            key = (row["page_id"], row["store"], row["store_id"])
            if key in seen_keys:                      # same candidate, several ads, one SERP
                continue
            seen_keys.add(key)
            rows.append(row)
        if rows:                                      # one short txn per keyword
            with begin(engine) as conn:
                for row in rows:
                    _, was_new = upsert_sweep_candidate(conn, row)
                    stats["candidates_new" if was_new else "candidates_seen"] += 1
        if heartbeat is not None:
            heartbeat()        # prove liveness each keyword so the watchdog (stale-
                               # heartbeat kill) never fells a slow-but-progressing sweep
        sleep(kw_delay)
    return stats
