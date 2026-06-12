"""Stage 1 of the FUNNEL pipeline — sibling to discovery/sweep.py, but it KEEPS web
destinations (the inverse of sweep.py's non_app drop) and writes discovery.funnel_candidates.
Shares only pure helpers (funnel_classify, classify_url). The app sweep is untouched.

Inline drops (cheapest-first): no link -> social host -> incumbent page likes -> store link
(belongs to the APP pipeline) -> web destination without a funnel signal.
Commits per keyword (short txns) so a mid-run throttle-kill keeps progress."""
import time
from datetime import datetime, timezone

from agf.classify import classify_url
from agf.db.engine import begin
from agf.db.funnel_candidates import upsert_funnel_candidate
from agf.discovery import funnel_classify as fc
from agf.discovery.taxonomy import all_entries
from agf.extract.errors import ExtractError
from agf.scoring.vetoes import INCUMBENT_PAGE_LIKES_MAX


def _start_date(epoch):
    if not epoch:
        return None
    try:
        return datetime.fromtimestamp(float(epoch), tz=timezone.utc).date()
    except (TypeError, ValueError, OSError):
        return None


def classify_funnel_candidate(ad, *, keyword: str, technique: str) -> tuple[str, dict | None]:
    """Fold one AdRecord into (disposition, funnel-row). Dispositions:
    'candidate' | 'no_link' | 'social' | 'incumbent_likes' | 'app' | 'not_funnel'."""
    if not ad.page_id or not ad.link_url:
        return "no_link", None
    if fc.host_of(ad.link_url) in fc.SOCIAL_HOSTS:
        return "social", None
    likes = ad.page_like_count
    if likes is not None and likes > INCUMBENT_PAGE_LIKES_MAX:
        return "incumbent_likes", None
    if classify_url(ad.link_url).kind in ("ios", "android"):
        return "app", None                       # store link -> the APP pipeline owns it
    if not fc.funnel_signal(ad, technique):
        return "not_funnel", None
    domain = fc.landing_domain(ad.link_url)
    if not domain:
        return "no_link", None
    return "candidate", {
        "page_id": ad.page_id, "page_name": ad.page_name, "page_like_count": likes,
        "landing_url": ad.link_url, "landing_domain": domain,
        "funnel_host": fc.matched_funnel_host(ad.link_url),
        "cta_type": ad.cta_type, "ad_start_date": _start_date(ad.start_date),
        "source_keyword": keyword, "technique": technique, **fc.parse_utms(ad.link_url),
    }


async def run_funnel_sweep(engine, extractor, *, entries=None, countries=("US",),
                           max_pages: int = 3, kw_delay: float = 3.0,
                           sleep=time.sleep) -> dict:
    """Sweep the funnel_phrase layer (default) and persist funnel candidates.
    Returns counters. Commits per keyword."""
    if entries is None:
        entries = all_entries(layer="funnel_phrase")
    stats = {"keywords_swept": 0, "serp_requests": 0, "raw_ads": 0,
             "candidates_new": 0, "candidates_seen": 0, "errors": 0,
             "drops": {"no_link": 0, "social": 0, "incumbent_likes": 0,
                       "app": 0, "not_funnel": 0}}
    for entry in entries:
        pages = min(entry.max_pages, max_pages)
        try:
            ads = await extractor.search(query=entry.keyword, countries=list(countries),
                                         search_type=entry.search_type, max_pages=pages)
        except ExtractError:
            stats["errors"] += 1
            sleep(kw_delay * 2)
            continue
        stats["keywords_swept"] += 1
        stats["serp_requests"] += pages
        stats["raw_ads"] += len(ads)
        rows, seen = [], set()
        for ad in ads:
            disp, row = classify_funnel_candidate(ad, keyword=entry.keyword, technique=entry.layer)
            if disp != "candidate":
                stats["drops"][disp] += 1
                continue
            key = (row["page_id"], row["landing_domain"])
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
        if rows:
            with begin(engine) as conn:
                for row in rows:
                    _, was_new = upsert_funnel_candidate(conn, row)
                    stats["candidates_new" if was_new else "candidates_seen"] += 1
        sleep(kw_delay)
    return stats
