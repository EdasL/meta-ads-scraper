from dataclasses import dataclass
from agf.parse import AdRecord

_SECONDS_PER_DAY = 86400


@dataclass
class AdvertiserScale:
    page_id: str
    page_name: str
    active_creative_count: int
    max_longevity_days: int


def longevity_days(start_epoch: int, now_epoch: int) -> int:
    """Whole days an ad has been running."""
    return max(0, (now_epoch - start_epoch) // _SECONDS_PER_DAY)


def aggregate_advertisers(ads: list[AdRecord], now_epoch: int) -> list[AdvertiserScale]:
    """Collapse ads to advertisers; count distinct active creatives + max longevity."""
    by_page: dict[str, dict] = {}
    for ad in ads:
        if not ad.is_active:
            continue
        bucket = by_page.setdefault(
            ad.page_id, {"name": ad.page_name, "ids": set(), "longevity": 0}
        )
        bucket["ids"].add(ad.ad_archive_id)
        if ad.start_date:
            bucket["longevity"] = max(bucket["longevity"], longevity_days(ad.start_date, now_epoch))
    return [
        AdvertiserScale(page_id=pid, page_name=b["name"],
                        active_creative_count=len(b["ids"]),
                        max_longevity_days=b["longevity"])
        for pid, b in by_page.items()
    ]
