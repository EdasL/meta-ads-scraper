"""The non-negotiable cross-check (master-plan §2): a candidate only counts as
"scaling on Meta" once the Ad Library confirms an ACTIVE creative running ≥14 days."""
from agf.scale import longevity_days


async def confirm_scaling_on_meta(extractor, page_id, now_epoch: int,
                                  min_longevity_days: int = 14) -> bool:
    """True iff the page has an active US ad whose longevity ≥ min_longevity_days."""
    ads = await extractor.search(query="", countries=["US"], search_type="page",
                                 view_all_page_id=page_id, active_status="ACTIVE")
    for ad in ads:
        if ad.is_active and ad.start_date and \
                longevity_days(ad.start_date, now_epoch) >= min_longevity_days:
            return True
    return False


async def run_scaling_confirm(extractor, page_ids, now_epoch: int,
                              min_longevity_days: int = 14) -> list[str]:
    """Return the subset of page_ids confirmed scaling on Meta (≥14d active)."""
    confirmed = []
    for pid in page_ids:
        if await confirm_scaling_on_meta(extractor, pid, now_epoch, min_longevity_days):
            confirmed.append(pid)
    return confirmed
