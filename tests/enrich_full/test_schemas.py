import dataclasses
import pytest
from agf.enrich_full.schemas import (
    ResolvedTarget, FunnelStep, PricingExtract, CreativeAsset, CompanyInfo, EnrichResult,
)


def test_construct_all_schemas():
    rt = ResolvedTarget(kind="ios", store_id="42", landing_url="https://l", final_url="https://f")
    fs = FunnelStep(step=1, url="https://f", screenshot_s3_key="k", title="t")
    pe = PricingExtract(prices=("9.99",), currency="USD", trial="7-day free")
    ca = CreativeAsset(type="image", sha256="s", phash="p", s3_key="k",
                       content_type="image/png", file_size=12, creative_id=3, dedup_of=None)
    ci = CompanyInfo(domain="x.com", domain_id=1, created_date=None, domain_age_days=100,
                     registrar="R", tech=("Stripe",), publisher="Acme")
    er = EnrichResult(advertiser_id=5, n_ads_processed=2, funnels=1, creatives=3, company=ci)
    assert rt.store_id == "42" and fs.step == 1 and pe.currency == "USD"
    assert ca.dedup_of is None and ci.tech == ("Stripe",) and er.company is ci


def test_schemas_are_frozen():
    rt = ResolvedTarget(kind="web", store_id=None, landing_url="l", final_url="f")
    with pytest.raises(dataclasses.FrozenInstanceError):
        rt.kind = "ios"


def test_optional_defaults():
    assert PricingExtract().prices == () and PricingExtract().currency is None
    assert EnrichResult(advertiser_id=1, n_ads_processed=0).company is None
