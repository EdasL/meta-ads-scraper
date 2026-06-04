from datetime import date, datetime
from sqlalchemy import text
from agf.parse import AdRecord
from agf.enrich import AppMeta
from agf.db import repo
from agf.enrich_full.company import (
    domain_age, builtwith_tech, publisher_from_app, enrich_company,
)


class FakeWhois:
    def __init__(self, rec): self.rec = rec
    def __call__(self, domain): return self.rec


def _rec(**kw):
    from types import SimpleNamespace
    base = dict(creation_date=datetime(2020, 1, 1), registrar="NameCheap")
    base.update(kw)
    return SimpleNamespace(**base)


def test_domain_age_computes_days_and_registrar():
    created, age, reg = domain_age("x.com", whois_fn=FakeWhois(_rec()), now=date(2026, 6, 4))
    assert created == date(2020, 1, 1) and reg == "NameCheap"
    assert age == (date(2026, 6, 4) - date(2020, 1, 1)).days


def test_domain_age_handles_list_dates_and_failures():
    rec = _rec(creation_date=[datetime(2021, 5, 1), datetime(2021, 6, 1)])
    created, age, _ = domain_age("x.com", whois_fn=FakeWhois(rec), now=date(2026, 6, 4))
    assert created == date(2021, 5, 1)

    def boom(_): raise RuntimeError("whois down")
    assert domain_age("x.com", whois_fn=boom) == (None, None, None)
    assert domain_age(None) == (None, None, None)


def test_builtwith_tech_detects_from_html_and_headers():
    html = '<script src="https://js.stripe.com/v3"></script><div data-reactroot>'
    tech = builtwith_tech(html, headers={"x-shopify-stage": "production"})
    assert "Stripe" in tech and "React" in tech and "Shopify" in tech


def test_publisher_from_app_accepts_meta_or_dict():
    m = AppMeta("1", "X", "Acme Inc", "Health & Fitness", "2026-01-01", 10, 4.0)
    assert publisher_from_app(m) == "Acme Inc"
    assert publisher_from_app({"developer": "Beta LLC"}) == "Beta LLC"
    assert publisher_from_app(None) is None


def _ad():
    return AdRecord("CADV", "PCO", "GemCo", True, 1_714_500_000, None, 1,
                    "INSTALL_MOBILE_APP", "Install", "https://getgem.com", "body")


def test_enrich_company_backfills_domain_id(pg_conn):
    adv = repo.upsert_advertiser(pg_conn, _ad())
    info = enrich_company(pg_conn, adv, domain="getgem.com",
                          html='<script src="https://js.stripe.com/v3"></script>',
                          app_meta=AppMeta("1", "X", "Acme", "G", "2026-01-01", 1, 4.0),
                          whois_fn=FakeWhois(_rec()), now=date(2026, 6, 4))
    assert info.domain == "getgem.com" and info.domain_id is not None
    assert "Stripe" in info.tech and info.publisher == "Acme"
    row = pg_conn.execute(text("SELECT domain_id FROM public.advertisers WHERE id=:i"),
                          {"i": adv}).first()
    assert row.domain_id == info.domain_id
    dom = pg_conn.execute(text("SELECT domain FROM public.domains WHERE id=:i"),
                          {"i": info.domain_id}).scalar()
    assert dom == "getgem.com"
