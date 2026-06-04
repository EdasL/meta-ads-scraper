"""Company/domain enrichment (master-plan §6 P6-T9): WHOIS domain age, a lightweight
BuiltWith-style tech fingerprint (page source + headers, no paid API), and the app
publisher. Backfills advertiser enrichment (§3.3 domain_id/page_category);
parent_company_id is left NULL (the Plan 5 veto tolerates it — see Scope decisions)."""
from datetime import date, datetime, timezone
from sqlalchemy import text
from agf.db import repo
from agf.enrich_full.schemas import CompanyInfo

# source-code signatures -> tech name (extends the Firehose-C spirit to general stack)
TECH_SIGNATURES = {
    "Shopify": ["cdn.shopify.com", "x-shopify", "Shopify.theme"],
    "Stripe": ["js.stripe.com", "stripe.com/v3"],
    "RevenueCat": ["api.revenuecat.com", "@revenuecat/purchases-js"],
    "Superwall": ["cdn.superwall.me", "superwall"],
    "Next.js": ["/_next/static", "__NEXT_DATA__"],
    "React": ["data-reactroot", "react.production.min.js", "__REACT_DEVTOOLS"],
    "Webflow": ["assets.website-files.com", "webflow.js"],
    "Meta Pixel": ["connect.facebook.net", "fbevents.js", "fbq("],
    "Google Analytics": ["googletagmanager.com/gtag", "google-analytics.com/analytics.js"],
    "Typeform": ["embed.typeform.com"],
    "Framer": ["framerusercontent.com"],
    "WordPress": ["wp-content/", "wp-includes/"],
}


def _coerce_date(v):
    if isinstance(v, list):
        v = next((x for x in v if x is not None), None)
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00")).date()
        except ValueError:
            return None
    return None


def domain_age(domain, *, whois_fn=None, now=None):
    """(created_date, age_days, registrar) via WHOIS. whois_fn injectable for tests;
    any failure degrades to (None, None, None)."""
    if not domain:
        return (None, None, None)
    if whois_fn is None:
        import whois
        whois_fn = whois.whois
    try:
        rec = whois_fn(domain)
    except Exception:
        return (None, None, None)
    get = (rec.get if isinstance(rec, dict) else lambda k: getattr(rec, k, None))
    created = _coerce_date(get("creation_date"))
    registrar = get("registrar")
    age = None
    if created:
        ref = now or datetime.now(timezone.utc).date()
        age = (ref - created).days
    return (created, age, registrar)


def builtwith_tech(html, headers=None) -> tuple:
    """Lightweight tech fingerprint from page source + response headers (substring
    match, no paid API). Returns sorted tech names."""
    hay = html or ""
    if headers:
        hay += " " + " ".join(f"{k}:{v}" for k, v in dict(headers).items())
    found = {name for name, sigs in TECH_SIGNATURES.items() if any(s in hay for s in sigs)}
    return tuple(sorted(found))


def publisher_from_app(app_meta) -> str | None:
    """The app's developer/seller name (AppMeta or dict)."""
    if app_meta is None:
        return None
    if isinstance(app_meta, dict):
        return app_meta.get("seller") or app_meta.get("developer")
    return getattr(app_meta, "seller", None)


def _upsert_domain(conn, domain) -> int | None:
    if not domain:
        return None
    return conn.execute(text("""
        INSERT INTO public.domains (domain) VALUES (:d)
        ON CONFLICT (domain) DO UPDATE SET domain = EXCLUDED.domain
        RETURNING id"""), {"d": domain}).scalar_one()


def enrich_company(conn, advertiser_id, *, domain=None, html=None, headers=None,
                   app_meta=None, page_category=None, whois_fn=None, now=None) -> CompanyInfo:
    """Compute + persist company enrichment: upsert the domain, backfill the
    advertiser's domain_id/page_category (§3.3), return the gathered CompanyInfo."""
    domain_id = _upsert_domain(conn, domain)
    created, age, registrar = domain_age(domain, whois_fn=whois_fn, now=now)
    tech = builtwith_tech(html, headers)
    publisher = publisher_from_app(app_meta)
    repo.upsert_advertiser_enrichment(conn, advertiser_id, domain_id=domain_id,
                                      page_category=page_category)
    return CompanyInfo(domain=domain, domain_id=domain_id, created_date=created,
                       domain_age_days=age, registrar=registrar, tech=tech,
                       publisher=publisher)
