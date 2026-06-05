"""Discovery orchestrator: Firehose B (+EU→US bridge) → Firehose C → upsert →
link apps → mandatory 14-day scaling-confirm. Only confirmed pages count."""
import sys
from agf.discovery.firehose_eu import run_eu_impressions_firehose, collapse_to_advertisers
from agf.discovery.bridge import run_bridge
from agf.discovery.firehose_fingerprint import fingerprint_search
from agf.discovery.link_apps import link_app_to_advertiser
from agf.discovery.confirm import run_scaling_confirm
from agf.discovery.seeds import Candidate, upsert_candidate


async def run_discovery(conn, extractor, now_epoch: int, *, eu_countries=("DE", "GB"),
                        min_running_days: int = 25, min_longevity_days: int = 14,
                        eu_max_pages: int = 5,
                        fingerprint_client=None, fingerprint_queries=()) -> dict:
    # Firehose B — EU impressions radar → advertiser pages running ≥ min_running_days.
    # eu_max_pages bounds the per-country sweep (the live loop caps it via AGF_EU_MAX_PAGES
    # so a sweep can't hammer Meta).
    ads = await run_eu_impressions_firehose(extractor, eu_countries, max_pages=eu_max_pages)
    page_cands = collapse_to_advertisers(ads, now_epoch, min_running_days)
    # EU → US bridge → richer US ads → app + page candidates
    bridge_cands = await run_bridge(extractor, [c.page_id for c in page_cands])
    # Firehose C — funnel-tech fingerprint (optional; web-native targets)
    funnel_cands: list[Candidate] = []
    if fingerprint_client is not None:
        for q in fingerprint_queries:
            for domain in fingerprint_search(fingerprint_client, q):
                funnel_cands.append(Candidate(source="firehose_fingerprint", kind="funnel",
                                              landing_url=f"https://{domain}", funnel_intent=True))

    all_cands = page_cands + bridge_cands + funnel_cands
    for c in all_cands:                                   # upsert into the spine (§3.3)
        upsert_candidate(conn, c)
    for c in all_cands:                                   # link app candidates → advertisers
        if c.kind == "app":
            await link_app_to_advertiser(conn, extractor, c)

    page_ids = sorted({c.page_id for c in all_cands if c.page_id})
    confirmed = await run_scaling_confirm(extractor, page_ids, now_epoch, min_longevity_days)
    return {"candidates": len(all_cands), "pages": len(page_ids), "confirmed": confirmed}


def make_discovery_handler(extractor, now_epoch, **kw):
    """Adapter closure (§3.9) so run_worker can drive discovery: handler(conn, _entity_id)."""
    import asyncio

    def handler(conn, _entity_id):
        result = asyncio.run(run_discovery(conn, extractor, now_epoch, **kw))
        return f"confirmed={len(result['confirmed'])}/{result['pages']}"
    return handler


if __name__ == "__main__":
    if "--dry-run" in sys.argv:
        print("discovery dry-run: Firehose B (EU impressions DE/GB) → EU→US bridge → "
              "Firehose C (funnel fingerprint) → upsert → link apps → 14d scaling-confirm")
        sys.exit(0)
    print("live run is wired programmatically via run_discovery(conn, extractor, now_epoch)")
