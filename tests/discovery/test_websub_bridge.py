"""Tests for the web-sub bridge stage (Task 7).

Schema adaptation note: the brief's _fake_refresh INSERT used column 'ad_archive_id',
which does not exist in public.ads. The real NOT NULL unique column is 'external_ad_id'
(constraint ads_external_ad_id_key). The ON CONFLICT clause targets that column instead.
All assertions from the brief are preserved unchanged.
"""
import os
import asyncio

import pytest
from sqlalchemy import text

from agf.db.engine import get_engine
from agf.db.migrate import apply_migrations
from agf.discovery import websub_bridge

DB = os.environ.get("AGF_TEST_DB_URL")
pytestmark = pytest.mark.skipif(not DB, reason="needs AGF_TEST_DB_URL")

LANDING_HTML = ('<html><head><meta property="og:title" content="Sleep Tight">'
                '<meta property="og:description" content="Sleep better."></head></html>')


async def _fake_refresh(engine, extractor, advertiser_id, page_id, **kw):
    # simulate a Meta scrape persisting one ad so the page becomes a real advertiser
    # Note: real column is 'external_ad_id', not 'ad_archive_id' (schema adaptation)
    with engine.begin() as c:
        c.execute(text(
            "INSERT INTO public.ads (external_ad_id, advertiser_id, is_active, start_date) "
            "VALUES (:aid, :adv, true, now()) ON CONFLICT (external_ad_id) DO NOTHING"),
            {"aid": f"ad-{page_id}", "adv": advertiser_id})
    return {"ads_stored": 1, "truncated": False, "flipped": 0}


def _cleanup(eng, page_id):
    """Remove all DB state seeded for page_id, deleting FK dependents in order."""
    with eng.begin() as c:
        ids = c.execute(text(
            "SELECT id FROM public.advertisers WHERE page_id=:pid"), {"pid": page_id}
        ).scalars().all()
        if ids:
            c.execute(text(
                "DELETE FROM adtech.app_advertiser_metrics WHERE advertiser_id=ANY(:i)"),
                {"i": ids})
            c.execute(text(
                "DELETE FROM adtech.advertiser_scores WHERE advertiser_id=ANY(:i)"),
                {"i": ids})
            c.execute(text(
                "DELETE FROM adtech.advertiser_growth WHERE advertiser_id=ANY(:i)"),
                {"i": ids})
            c.execute(text(
                "DELETE FROM adtech.advertiser_explainers WHERE advertiser_id=ANY(:i)"),
                {"i": ids})
            c.execute(text(
                "DELETE FROM frontend.review_decisions WHERE advertiser_id=ANY(:i)"),
                {"i": ids})
            c.execute(text(
                "DELETE FROM frontend.saved_gems WHERE advertiser_id=ANY(:i)"),
                {"i": ids})
            c.execute(text(
                "DELETE FROM discovery.candidates WHERE kept_advertiser_id=ANY(:i)"),
                {"i": ids})
            c.execute(text(
                "DELETE FROM public.creatives cr USING public.ads ad "
                "WHERE cr.ad_id=ad.id AND ad.advertiser_id=ANY(:i)"),
                {"i": ids})
            c.execute(text(
                "DELETE FROM public.ads WHERE advertiser_id=ANY(:i)"), {"i": ids})
            c.execute(text(
                "DELETE FROM public.advertisers WHERE id=ANY(:i)"), {"i": ids})
        c.execute(text(
            "DELETE FROM discovery.funnel_candidates WHERE page_id=:pid"), {"pid": page_id})


def _seed_confirmed_websub(eng, page_id, domain):
    """Seed a confirmed web-sub funnel row, cleaning up any prior state first."""
    _cleanup(eng, page_id)
    with eng.begin() as c:
        c.execute(text("""
            INSERT INTO discovery.funnel_candidates
              (page_id, page_name, page_like_count, landing_url, landing_domain,
               funnel_host, source_keyword, technique, status, payoff_kind, confirmed_at)
            VALUES (:pid, 'Sleep Tight', 0, :url, :dom, :dom, 'kw', 'funnel',
                    'confirmed', 'web_sub', now())
        """), {"pid": page_id, "url": f"https://{domain}/start", "dom": domain})


def test_bridge_creates_advertiser_with_web_identity(monkeypatch):
    eng = get_engine(DB)
    apply_migrations(eng)
    monkeypatch.setattr(websub_bridge, "refresh_one_advertiser", _fake_refresh)
    _seed_confirmed_websub(eng, "WSBR1", "trysleeptight.app")
    stats = asyncio.run(websub_bridge.run_websub_bridge(
        eng, extractor=object(), fetch_text=lambda u: LANDING_HTML, limit=10))
    assert stats["bridged"] >= 1
    with eng.connect() as c:
        row = c.execute(text(
            "SELECT web_payoff_kind, web_name, web_landing_url FROM public.advertisers "
            "WHERE page_id='WSBR1'")).one()
    assert row.web_payoff_kind == "web_sub"
    assert row.web_name == "Sleep Tight"


def test_bridge_skips_pages_already_advertisers(monkeypatch):
    eng = get_engine(DB)
    apply_migrations(eng)
    monkeypatch.setattr(websub_bridge, "refresh_one_advertiser", _fake_refresh)
    _seed_confirmed_websub(eng, "WSBR2", "app.bossed.ai")
    # run once to bridge it
    asyncio.run(websub_bridge.run_websub_bridge(
        eng, extractor=object(), fetch_text=lambda u: LANDING_HTML, limit=10))
    # run again — WSBR2 is now an advertiser, so pending list must exclude it
    with eng.connect() as c:
        pend = websub_bridge.pending_websub_pages(c, 10)
    assert all(r.page_id != "WSBR2" for r in pend)
