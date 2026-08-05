"""Fresh-ads phase of enrich_one ("Refresh data" now re-scrapes the listing):
page gate, upsert + vanish flip, fail-soft on extract error and timeout, and
the _default_enrich ordering. ENRICHADS* sentinels; engine tests COMMIT
(refresh_one_advertiser owns its txn), so each test cleans up first."""
import asyncio
from types import SimpleNamespace

from sqlalchemy import text

from agf.db import repo
from agf.db.engine import begin
from agf.enrich_full import enrich_one
from agf.enrich_full.enrich_one import _refresh_ads_listing
from agf.extract.errors import ExtractError
from tests.discovery.conftest import FakeExtractor, make_ad


def _cleanup(engine):
    with begin(engine) as conn:
        ids = conn.execute(text(
            "SELECT id FROM public.advertisers WHERE page_id LIKE 'ENRICHADS%'"
        )).scalars().all()
        if not ids:
            return
        ad_ids = conn.execute(text(
            "SELECT id FROM public.ads WHERE advertiser_id = ANY(:i)"), {"i": ids}
        ).scalars().all()
        if ad_ids:
            conn.execute(text("DELETE FROM public.creatives WHERE ad_id = ANY(:i)"), {"i": ad_ids})
        conn.execute(text("DELETE FROM public.ads WHERE advertiser_id = ANY(:i)"), {"i": ids})
        conn.execute(text("DELETE FROM adtech.app_advertiser_metrics WHERE advertiser_id = ANY(:i)"), {"i": ids})
        conn.execute(text("DELETE FROM public.advertisers WHERE id = ANY(:i)"), {"i": ids})


def _seed_advertiser(engine, page_id):
    with begin(engine) as conn:
        return repo.upsert_advertiser(
            conn, SimpleNamespace(page_id=page_id, page_name="EnrichAds", page_like_count=10))


def test_refresh_upserts_and_flips_vanished(_migrated_engine):
    _cleanup(_migrated_engine)
    adv = _seed_advertiser(_migrated_engine, "ENRICHADSA")

    ext = FakeExtractor(listing_ads={"ENRICHADSA": [
        make_ad("ea-1", "ENRICHADSA", "EnrichAds", "https://x/app"),
        make_ad("ea-2", "ENRICHADSA", "EnrichAds", "https://x/app"),
    ]})
    got = _refresh_ads_listing(_migrated_engine, adv, extractor=ext)
    assert got is not None and got["ads_stored"] == 2

    with begin(_migrated_engine) as conn:
        n = conn.execute(text(
            "SELECT count(*) FROM public.ads WHERE advertiser_id=:a"), {"a": adv}).scalar()
    assert n == 2

    # Second pull: ea-2 vanished from the listing -> flipped inactive.
    ext2 = FakeExtractor(listing_ads={"ENRICHADSA": [
        make_ad("ea-1", "ENRICHADSA", "EnrichAds", "https://x/app"),
    ]})
    got2 = _refresh_ads_listing(_migrated_engine, adv, extractor=ext2)
    assert got2 is not None and got2["flipped"] == 1
    with begin(_migrated_engine) as conn:
        active = conn.execute(text(
            "SELECT external_ad_id FROM public.ads WHERE advertiser_id=:a AND is_active"),
            {"a": adv}).scalars().all()
    assert active == ["ea-1"]
    _cleanup(_migrated_engine)


def test_missing_advertiser_skips_without_calling_extractor(_migrated_engine):
    class Boom:
        async def search_listing(self, **kw):
            raise AssertionError("must not be called")
    assert _refresh_ads_listing(_migrated_engine, -1, extractor=Boom()) is None


def test_extract_error_is_fail_soft(_migrated_engine):
    _cleanup(_migrated_engine)
    adv = _seed_advertiser(_migrated_engine, "ENRICHADSB")

    class Failing:
        async def search_listing(self, **kw):
            raise ExtractError("meta said no")
    assert _refresh_ads_listing(_migrated_engine, adv, extractor=Failing()) is None
    _cleanup(_migrated_engine)


def test_slow_listing_hits_budget_and_fails_soft(_migrated_engine):
    _cleanup(_migrated_engine)
    adv = _seed_advertiser(_migrated_engine, "ENRICHADSC")

    class Slow:
        async def search_listing(self, **kw):
            await asyncio.sleep(1)
            return [], False
    assert _refresh_ads_listing(_migrated_engine, adv, extractor=Slow(), timeout=0.05) is None
    _cleanup(_migrated_engine)


def test_default_enrich_runs_listing_phase_first(monkeypatch, _migrated_engine):
    calls = []
    monkeypatch.setattr(enrich_one, "_refresh_ads_listing",
                        lambda *a, **k: calls.append("listing"))
    monkeypatch.setattr(enrich_one, "prefetch_creative_urls",
                        lambda *a, **k: (calls.append("prefetch"), {})[1])
    monkeypatch.setattr(enrich_one, "_build_deps", lambda *a, **k: None)

    async def fake_enrich_gem(conn, aid, **kw):
        calls.append("enrich")
    monkeypatch.setattr(enrich_one, "enrich_gem", fake_enrich_gem)

    enrich_one._default_enrich(_migrated_engine, 1, extractor=None, http_client=None)
    assert calls == ["listing", "prefetch", "enrich"]


def test_default_enrich_skips_listing_when_ads_refresh_false(monkeypatch, _migrated_engine):
    """The continuous pipeline lane must never pay for the listing scrape."""
    def boom(*a, **k):
        raise AssertionError("_refresh_ads_listing must not be called")
    monkeypatch.setattr(enrich_one, "_refresh_ads_listing", boom)

    calls = []
    monkeypatch.setattr(enrich_one, "prefetch_creative_urls",
                        lambda *a, **k: (calls.append("prefetch"), {})[1])
    monkeypatch.setattr(enrich_one, "_build_deps", lambda *a, **k: None)

    async def fake_enrich_gem(conn, aid, **kw):
        calls.append("enrich")
    monkeypatch.setattr(enrich_one, "enrich_gem", fake_enrich_gem)

    enrich_one._default_enrich(_migrated_engine, 1, extractor=None,
                               http_client=None, ads_refresh=False)
    assert calls == ["prefetch", "enrich"]


def _main_ads_refresh_flag(monkeypatch, status_table):
    """Run main() with run_job/_default_enrich stubbed; return the ads_refresh
    kwarg main() wired into the enrich_call lambda."""
    seen = {}
    monkeypatch.setattr(enrich_one, "get_engine", lambda *a, **k: object())
    monkeypatch.setattr(enrich_one, "_default_enrich",
                        lambda eng, aid, **kw: seen.update(kw))

    def fake_run_job(engine, *, advertiser_id, job_id, enrich_call, **kw):
        enrich_call(engine, advertiser_id)
    monkeypatch.setattr(enrich_one, "run_job", fake_run_job)

    enrich_one.main(["--advertiser-id", "1", "--job-id", "2",
                     "--status-table", status_table])
    return seen["ads_refresh"]


def test_main_enables_ads_refresh_only_for_enrich_jobs(monkeypatch):
    assert _main_ads_refresh_flag(monkeypatch, "enrich_jobs") is True
    assert _main_ads_refresh_flag(monkeypatch, "pipeline_jobs") is False
