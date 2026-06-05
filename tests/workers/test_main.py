"""The live worker loop, dry-run end-to-end against real Postgres with the in-src
FakeExtractor (zero Meta traffic). Proves the composition the ops `worker` service
runs: build extractor → run_discovery → enrich flagged/confirmed advertisers via
run_worker. Skips cleanly without AGF_TEST_DB_URL; cleans up its FAKE* sentinels."""
import os
import subprocess
import sys
from datetime import date

from sqlalchemy import text

from agf.db.engine import begin
from agf.workers.main import run_once, build_extractor, dry_run_deps_factory

NOW = 1_749_000_000          # ~2026-06-04, matches tests/discovery/conftest
FAKE_PAGES = ("FAKE1", "FAKE2")
FAKE_STORE_IDS = ("444000", "555000")


def _clean_fake(engine):
    with begin(engine) as conn:
        ids = conn.execute(text("SELECT id FROM public.advertisers WHERE page_id = ANY(:p)"),
                           {"p": list(FAKE_PAGES)}).scalars().all()
        if ids:
            conn.execute(text("DELETE FROM logging.crawl_log "
                              "WHERE entity_type='advertiser' AND entity_id = ANY(:i)"), {"i": ids})
            for t in ("adtech.advertiser_scores", "adtech.app_advertiser_metrics"):
                conn.execute(text(f"DELETE FROM {t} WHERE advertiser_id = ANY(:i)"), {"i": ids})
            conn.execute(text("DELETE FROM public.funnels f USING public.ads a "
                              "WHERE f.ad_id=a.id AND a.advertiser_id = ANY(:i)"), {"i": ids})
            conn.execute(text("DELETE FROM public.creatives c USING public.ads a "
                              "WHERE c.ad_id=a.id AND a.advertiser_id = ANY(:i)"), {"i": ids})
            conn.execute(text("UPDATE public.advertisers SET parent_company_id=NULL, domain_id=NULL "
                              "WHERE id = ANY(:i)"), {"i": ids})
            conn.execute(text("DELETE FROM public.ads WHERE advertiser_id = ANY(:i)"), {"i": ids})
            conn.execute(text("DELETE FROM public.advertisers WHERE id = ANY(:i)"), {"i": ids})
        conn.execute(text("DELETE FROM public.apps WHERE store_id = ANY(:s)"),
                     {"s": list(FAKE_STORE_IDS)})


def test_dry_run_sweep_discovers_confirms_and_enriches(_migrated_engine):
    eng = _migrated_engine
    _clean_fake(eng)
    try:
        ex = build_extractor(dry_run=True, now_epoch=NOW)   # in-src fake, no network
        summary = run_once(eng, ex, now_epoch=NOW, as_of=date(2026, 6, 4), dry_run=True,
                           deps_factory=dry_run_deps_factory(ex), score_after=False)
        # discovery: both seeded EU pages survive the 25d collapse + 14d confirm
        assert summary["discovery"]["pages"] == 2
        assert set(summary["discovery"]["confirmed"]) == set(FAKE_PAGES)
        # enrich: the loop drove the enrich handler over both confirmed advertisers
        assert len(summary["enrich_candidates"]) == 2
        assert len(summary["enriched"]) == 2
        assert all(isinstance(v, str) for v in summary["enriched"].values())
        # each enrich recorded a crawl_log row (so the staleness scheduler won't re-run it)
        with eng.connect() as conn:
            n = conn.execute(text(
                "SELECT count(*) FROM logging.crawl_log WHERE entity_type='advertiser' "
                "AND entity_id = ANY(:i)"), {"i": summary["enrich_candidates"]}).scalar()
        assert n == 2
    finally:
        _clean_fake(eng)


def test_dry_run_cli_runs_once_and_exits_zero(_migrated_engine):
    """`python -m agf.workers.main --dry-run` — the literal command the ops worker
    runs in dry-run — boots, sweeps once against the test DB, and exits 0."""
    db_url = os.environ.get("AGF_TEST_DB_URL")
    _clean_fake(_migrated_engine)
    try:
        env = {**os.environ, "PYTHONPATH": "src", "AGF_DATABASE_URL": db_url,
               "AGF_DRY_RUN": "1"}
        out = subprocess.run([sys.executable, "-m", "agf.workers.main"],
                             capture_output=True, text=True, env=env, timeout=120)
        assert out.returncode == 0, out.stderr
        assert "[worker] starting — mode=DRY-RUN" in out.stdout
        assert "confirmed=2" in out.stdout
    finally:
        _clean_fake(_migrated_engine)
