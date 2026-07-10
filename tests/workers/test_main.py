"""The live worker loop, dry-run end-to-end against real Postgres with the in-src
FakeExtractor (zero Meta traffic). Proves the composition the ops `worker` service
runs: build extractor → sweep → triage → keeper-history backfill → enrich keepers
via run_worker. Skips cleanly without AGF_TEST_DB_URL; cleans up its FAKE*
sentinels."""
import os
import subprocess
import sys
from datetime import date

from sqlalchemy import text

from agf.db.engine import begin
from agf.workers.main import (run_once, build_extractor, build_extractors,
                              discovery_due, _env_bool)

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


def test_build_extractors_dry_run_is_single_fake():
    exts = build_extractors(dry_run=True, now_epoch=NOW, k=3)
    assert len(exts) == 1                       # dry-run collapses to one zero-network fake


def _spy_extractor_pools(monkeypatch):
    """Replace Extractor with a recorder that captures each worker's ProxyPool list.
    build_extractors does `from agf.extract.facade import Extractor`, which reads the
    module attribute at call time, so patching it here takes effect."""
    import agf.extract.facade as facade
    pools = []

    class _RecordingExtractor:
        def __init__(self, *, proxies=None, **kw):
            pools.append(list(proxies._proxies) if proxies is not None else None)

    monkeypatch.setattr(facade, "Extractor", _RecordingExtractor)
    return pools


def test_build_extractors_k_disjoint_proxy_slices(monkeypatch):
    # AGF_PROXIES has >= k entries -> one Extractor per disjoint round-robin slice.
    monkeypatch.setenv("AGF_PROXIES", ",".join(f"p{i}" for i in range(6)))
    pools = _spy_extractor_pools(monkeypatch)
    exts = build_extractors(dry_run=False, now_epoch=NOW, k=3)
    assert len(exts) == 3                        # K independent extractors
    assert pools == [["p0", "p3"], ["p1", "p4"], ["p2", "p5"]]   # disjoint, complete
    flat = sorted(p for s in pools for p in s)
    assert flat == [f"p{i}" for i in range(6)]


def test_build_extractors_fewer_proxies_than_workers_share_full_pool(monkeypatch):
    # Fewer proxies than workers: each worker gets its own pool over the FULL list.
    monkeypatch.setenv("AGF_PROXIES", "p0,p1")
    pools = _spy_extractor_pools(monkeypatch)
    exts = build_extractors(dry_run=False, now_epoch=NOW, k=3)
    assert len(exts) == 3
    assert pools == [["p0", "p1"], ["p0", "p1"], ["p0", "p1"]]


def test_build_extractors_empty_proxies_is_single_worker(monkeypatch):
    # No proxy pool: K direct-connection workers would all egress from the one host IP
    # (rate-limit footgun, no concurrency benefit) -> collapse to a single worker.
    monkeypatch.delenv("AGF_PROXIES", raising=False)
    pools = _spy_extractor_pools(monkeypatch)
    exts = build_extractors(dry_run=False, now_epoch=NOW, k=3)
    assert len(exts) == 1                        # single direct-connection worker
    assert pools == [[]]                          # one Extractor over the empty pool


class _Stop(Exception):
    pass


def test_production_sweep_calls_run_parallel_sweep(monkeypatch, _migrated_engine):
    # The non-dry-run sweep branch must route through run_parallel_sweep with K
    # extractors from build_extractors — not the single-extractor run_sweep.
    import agf.workers.main as m

    calls = {}

    def fake_build_extractors(dry_run, now_epoch, k):
        calls["k"] = k
        assert dry_run is False
        return [object() for _ in range(k)]

    def fake_run_parallel_sweep(engine, extractors, **kw):
        calls["n_extractors"] = len(extractors)
        return {"keywords_swept": 0, "candidates_new": 0, "candidates_seen": 0,
                "errors": 0, "drops": {}}

    def boom(*a, **k):
        raise AssertionError("production sweep must not call run_sweep")

    monkeypatch.setattr(m, "build_extractors", fake_build_extractors)
    monkeypatch.setattr(m, "run_parallel_sweep", fake_run_parallel_sweep)
    monkeypatch.setattr(m, "run_sweep", boom)
    monkeypatch.setenv("AGF_SWEEP_WORKERS", "4")
    # Stop right after the sweep branch (in triage) so we don't exercise later stages.
    def _stop_triage(*a, **k):
        raise _Stop()
    monkeypatch.setattr(m, "run_triage", _stop_triage)

    try:
        run_once(_migrated_engine, object(),
                 now_epoch=NOW, as_of=date(2026, 6, 4), dry_run=False,
                 http_client=object(), continuous_discovery=True,
                 funnel_daily=False, corpus_refresh_daily=False,
                 generic_deep_enabled=False, flywheel_daily=False,
                 release_mint_enabled=False)
    except _Stop:
        pass
    assert calls["k"] == 4 and calls["n_extractors"] == 4


def test_dry_run_sweep_triages_and_enriches(_migrated_engine):
    eng = _migrated_engine
    _clean_fake(eng)
    try:
        ex = build_extractor(dry_run=True, now_epoch=NOW)   # in-src fake, no network
        summary = run_once(eng, ex, now_epoch=NOW, as_of=date(2026, 6, 4), dry_run=True)
        # sweep: both demo keyword ads fold into NEW candidates (small pages, store links)
        assert summary["sweep"]["candidates_new"] == 2
        assert summary["sweep"]["drops"]["social"] == 0
        # triage: the offline lookup keeps both -> promoted advertisers + apps
        assert summary["triage"]["kept"] == 2 and summary["triage"]["dropped"] == {}
        # keeper history: both keepers got their page history pulled (>= tolerates
        # stray committed keepers in the shared DB) and stored at least the demo ads
        kh = summary["keeper_history"]
        assert kh["keepers_done"] >= 2 and kh["ads_stored"] >= 2 and kh["errors"] == 0
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
        # keeper history closed the unscoreable-keeper gap: each kept advertiser now
        # has its demo ad in public.ads AND a 16-week reconstructed series in
        # adtech.app_advertiser_metrics (scoped to the FAKE sentinels — shared DB).
        with eng.connect() as conn:
            hist = conn.execute(text(
                "SELECT adv.page_id, count(DISTINCT a.id) AS ads, "
                "       count(DISTINCT m.week_start) AS wks "
                "FROM public.advertisers adv "
                "LEFT JOIN public.ads a ON a.advertiser_id = adv.id "
                "LEFT JOIN adtech.app_advertiser_metrics m ON m.advertiser_id = adv.id "
                "WHERE adv.page_id = ANY(:p) GROUP BY adv.page_id"),
                {"p": list(FAKE_PAGES)}).all()
        assert {r.page_id: (r.ads, r.wks) for r in hist} == \
            {"FAKE1": (1, 16), "FAKE2": (1, 16)}
    finally:
        _clean_fake(eng)


def test_continuous_discovery_is_default_on(monkeypatch):
    """The brand-DB build setting: AGF_CONTINUOUS_DISCOVERY defaults ON, and an
    explicit 0/false turns it off — so the worker sweeps every tick unless asked not to."""
    monkeypatch.delenv("AGF_CONTINUOUS_DISCOVERY", raising=False)
    assert _env_bool("AGF_CONTINUOUS_DISCOVERY", True) is True
    monkeypatch.setenv("AGF_CONTINUOUS_DISCOVERY", "0")
    assert _env_bool("AGF_CONTINUOUS_DISCOVERY", True) is False
    monkeypatch.setenv("AGF_CONTINUOUS_DISCOVERY", "false")
    assert _env_bool("AGF_CONTINUOUS_DISCOVERY", True) is False


def test_continuous_bypasses_the_daily_gate(pg_conn):
    """With a finished sweep_runs row for today, the daily gate would normally skip
    discovery — continuous mode sweeps anyway. Rolled back by the pg_conn fixture."""
    today = date(2026, 6, 4)
    pg_conn.execute(text(
        "INSERT INTO discovery.sweep_runs (started_at, finished_at) "
        "VALUES (:d, now())"), {"d": today})
    # daily-gate path: a finished run today suppresses the sweep
    assert discovery_due(pg_conn, today, dry_run=False, continuous=False) is False
    # continuous path: sweep runs regardless of the finished run
    assert discovery_due(pg_conn, today, dry_run=False, continuous=True) is True
    # dry-runs always sweep (offline)
    assert discovery_due(pg_conn, today, dry_run=True, continuous=False) is True


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
        assert " hist=" in out.stdout
    finally:
        _clean_fake(_migrated_engine)
