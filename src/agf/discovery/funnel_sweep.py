"""Stage 1 of the FUNNEL pipeline — sibling to discovery/sweep.py, but it KEEPS web
destinations (the inverse of sweep.py's non_app drop) and writes discovery.funnel_candidates.
Shares only pure helpers (funnel_classify, classify_url). The app sweep is untouched.

Inline drops (cheapest-first): no link -> social host -> incumbent page likes -> store link
(belongs to the APP pipeline) -> web destination without a funnel signal.
Commits per keyword (short txns) so a mid-run throttle-kill keeps progress."""
import time

from agf.classify import classify_url
from agf.db.candidates import upsert_sweep_candidate
from agf.db.engine import begin
from agf.db.funnel_candidates import upsert_funnel_candidate
from agf.discovery import funnel_classify as fc
from agf.discovery.taxonomy import all_entries
from agf.extract.errors import ExtractError
from agf.scoring.vetoes import FUNNEL_INCUMBENT_PAGE_LIKES_MAX


def classify_funnel_candidate(ad, *, keyword: str, technique: str) -> tuple[str, dict | None]:
    """Fold one AdRecord into (disposition, row). Dispositions:
    'candidate' | 'no_link' | 'social' | 'incumbent_likes' | 'app' | 'not_funnel'.

    For 'candidate' the row is the discovery.funnel_candidates dict. For 'app' (a
    funnel-phrase ad whose link is a DIRECT store URL — an app-backed funnel) the row
    is a discovery.candidates dict so the sweep can route it into the APP pipeline
    instead of dropping it; None when there's no parseable store_id to resolve the app.
    All other dispositions carry None."""
    if not ad.page_id or not ad.link_url:
        return "no_link", None
    if fc.host_of(ad.link_url) in fc.SOCIAL_HOSTS:
        return "social", None
    likes = ad.page_like_count
    if likes is not None and likes > FUNNEL_INCUMBENT_PAGE_LIKES_MAX:
        return "incumbent_likes", None
    intent = classify_url(ad.link_url)
    if intent.kind in ("ios", "android"):
        if not intent.store_id:
            return "app", None                   # store link but no id to resolve the app
        return "app", {                          # app-backed funnel -> bridge to APP pipeline
            "page_id": ad.page_id, "page_name": ad.page_name, "page_like_count": likes,
            "store": intent.kind, "store_id": intent.store_id, "link_url": ad.link_url,
            "cta_type": ad.cta_type, "ad_start_date": fc.start_date_of(ad.start_date),
            "source_keyword": keyword, "technique": "funnel_sweep",
        }
    return fc.fold_web_funnel(ad, keyword=keyword, technique=technique)


async def run_funnel_sweep(engine, extractor, *, entries=None, countries=("US",),
                           max_pages: int = 3, kw_delay: float = 3.0,
                           sleep=time.sleep) -> dict:
    """Sweep the funnel_phrase layer (default) and persist funnel candidates.
    Returns counters. Commits per keyword."""
    if entries is None:
        entries = all_entries(layer="funnel_phrase")
    stats = {"keywords_swept": 0, "serp_requests": 0, "raw_ads": 0,
             "candidates_new": 0, "candidates_seen": 0,
             "bridged_app_new": 0, "bridged_app_seen": 0, "errors": 0,
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
        rows, app_rows, seen, app_seen = [], [], set(), set()
        for ad in ads:
            disp, row = classify_funnel_candidate(ad, keyword=entry.keyword, technique=entry.layer)
            if disp == "candidate":
                key = (row["page_id"], row["landing_domain"])
                if key in seen:
                    continue
                seen.add(key)
                rows.append(row)
            elif disp == "app":
                # Counted as a funnel-pipeline drop (it isn't a web funnel), but the
                # app-backed ones carry a candidate row we route to the APP pipeline.
                stats["drops"]["app"] += 1
                if row is not None:
                    akey = (row["page_id"], row["store"], row["store_id"])
                    if akey not in app_seen:
                        app_seen.add(akey)
                        app_rows.append(row)
            else:
                stats["drops"][disp] += 1
        if rows:
            with begin(engine) as conn:
                for row in rows:
                    _, was_new = upsert_funnel_candidate(conn, row)
                    stats["candidates_new" if was_new else "candidates_seen"] += 1
        if app_rows:
            # Bridge: app-backed funnels found via funnel phrases (which the app sweep's
            # niche keywords miss) join the same discovery.candidates pool, so run_triage
            # resolves their app and they reach the feed under the normal app vetoes.
            with begin(engine) as conn:
                for row in app_rows:
                    _, was_new = upsert_sweep_candidate(conn, row)
                    stats["bridged_app_new" if was_new else "bridged_app_seen"] += 1
        sleep(kw_delay)
    return stats
