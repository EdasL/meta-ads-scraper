"""The live worker loop, dry-run end-to-end against real Postgres with the in-src
FakeExtractor (zero Meta traffic). Proves the composition the ops `worker` service
runs: build extractor → sweep → triage → enrich keepers via run_worker. Skips
cleanly without AGF_TEST_DB_URL; cleans up its FAKE* sentinels."""
import os
import subprocess
import sys
from datetime import date, timedelta

from sqlalchemy import text

from agf.db.engine import begin
from agf.workers.main import run_once, build_extractor

NOW = 1_749_000_000          # ~2026-06-04, matches tests/discovery/conftest
FAKE_PAGES = ("FAKE1", "FAKE2")
FAKE_STORE_IDS = ("444000", "555000")


def _clean_fake(engine):
    with begin(engine) as conn:
        # candidates reference advertisers (kept_advertiser_id FK) — clear them BEFORE
        # the advertisers delete below or it trips the foreign key.
        conn.execute(text("DELETE FROM discovery.candidates WHERE page_id = ANY(:p)"),
                     {"p": list(FAKE_PAGES)})
        # run_once's triage processes EVERY committed 'new' row; agf_it is a scratch DB,
        # so clear strays other suites may have left, or kept-counts won't be exactly 2.
        conn.execute(text("DELETE FROM discovery.candidates WHERE status = 'new'"))
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


def test_dry_run_sweep_triages_and_enriches(_migrated_engine):
    eng = _migrated_engine
    _clean_fake(eng)
    try:
        ex = build_extractor(dry_run=True, now_epoch=NOW)   # in-src fake, no network
        summary = run_once(eng, ex, now_epoch=NOW, as_of=date(2026, 6, 4), dry_run=True,
                           score_after=False)
        # sweep: both demo keyword ads fold into NEW candidates (small pages, store links)
        assert summary["sweep"]["candidates_new"] == 2
        assert summary["sweep"]["drops"]["social"] == 0
        # triage: the offline lookup keeps both -> promoted advertisers + apps
        assert summary["triage"]["kept"] == 2 and summary["triage"]["dropped"] == {}
        # enrich: the loop drove the enrich handler over both kept advertisers
        assert len(summary["enrich_candidates"]) == 2
        assert len(summary["enriched"]) == 2
        assert all(isinstance(v, str) for v in summary["enriched"].values())
        with eng.connect() as conn:
            n = conn.execute(text(
                "SELECT count(*) FROM logging.crawl_log WHERE entity_type='advertiser' "
                "AND entity_id = ANY(:i)"), {"i": summary["enrich_candidates"]}).scalar()
            kept_rows = conn.execute(text(
                "SELECT count(*) FROM discovery.candidates "
                "WHERE page_id = ANY(:p) AND status='kept'"), {"p": list(FAKE_PAGES)}).scalar()
            apps = conn.execute(text(
                "SELECT count(*) FROM public.apps WHERE store_id = ANY(:s) AND name IS NOT NULL"),
                {"s": list(FAKE_STORE_IDS)}).scalar()
        assert n == 2 and kept_rows == 2 and apps == 2
        # velocity tracker: the GLOBAL weekly rollup ran (only_ids=None). Keepers have
        # no ads yet, so a clean DB legitimately rolls up 0 rows — assert instead that
        # no advertiser with active ads is missing this week's metrics row.
        assert summary["snapshot_weeks"] >= 0
        wk = date(2026, 6, 4) - timedelta(days=date(2026, 6, 4).weekday())
        with eng.connect() as conn:
            missing = conn.execute(text(
                "SELECT count(DISTINCT a.advertiser_id) FROM public.ads a "
                "WHERE a.is_active AND a.advertiser_id IS NOT NULL AND NOT EXISTS "
                "(SELECT 1 FROM adtech.app_advertiser_metrics m "
                " WHERE m.advertiser_id = a.advertiser_id AND m.week_start = :wk)"),
                {"wk": wk}).scalar()
        assert missing == 0
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
        assert "kept=2" in out.stdout
    finally:
        _clean_fake(_migrated_engine)
