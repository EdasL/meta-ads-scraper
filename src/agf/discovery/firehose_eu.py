"""Firehose B — the EU impressions radar. Keyword-free, impressions-sorted Ad Library
sweep over EU countries (DSA exposes reach there); collapse ad-level → advertiser and
keep those running long enough to be worth bridging to US."""
from agf.scale import aggregate_advertisers
from agf.discovery.seeds import Candidate


async def run_eu_impressions_firehose(extractor, countries=("DE", "GB"), max_pages: int = 5):
    """Sweep each EU country with an empty query sorted by impressions; return all ads."""
    ads = []
    for cc in countries:
        ads += await extractor.search(query="", countries=[cc], sort_by_impressions=True,
                                      max_pages=max_pages)
    return ads


def collapse_to_advertisers(ads, now_epoch: int, min_running_days: int = 25) -> list[Candidate]:
    """Collapse ads → advertisers (Plan 1 aggregate) and keep only those whose longest
    active creative has run ≥ min_running_days (drops short-lived test budgets)."""
    scales = aggregate_advertisers(ads, now_epoch)
    return [
        Candidate(source="firehose_eu", kind="page", page_id=s.page_id, page_name=s.page_name)
        for s in scales if s.max_longevity_days >= min_running_days
    ]
