"""Frozen result schemas for enrichment (master-plan §6 P6-T1)."""
from dataclasses import dataclass


@dataclass(frozen=True)
class ResolvedTarget:
    kind: str                 # 'ios' | 'android' | 'web' | 'none'
    store_id: str | None
    landing_url: str          # the ad's original link_url
    final_url: str            # after redirect resolution


@dataclass(frozen=True)
class FunnelStep:
    step: int
    url: str
    screenshot_s3_key: str | None
    title: str | None


@dataclass(frozen=True)
class PricingExtract:
    prices: tuple = ()        # ('9.99', '59.99')
    currency: str | None = None
    trial: str | None = None


@dataclass(frozen=True)
class CreativeAsset:
    type: str                 # 'image' | 'video'
    sha256: str
    phash: str | None
    s3_key: str | None
    content_type: str | None
    file_size: int | None
    creative_id: int | None   # row id (existing on dedup, new otherwise)
    dedup_of: int | None      # existing creative id if this was a dup, else None


@dataclass(frozen=True)
class CompanyInfo:
    domain: str | None
    domain_id: int | None
    created_date: object | None    # date | None
    domain_age_days: int | None
    registrar: str | None
    tech: tuple = ()
    publisher: str | None = None


@dataclass(frozen=True)
class EnrichResult:
    advertiser_id: int
    n_ads_processed: int
    funnels: int = 0
    creatives: int = 0
    company: CompanyInfo | None = None
