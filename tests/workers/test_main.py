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


def test_production_run_once_enqueues_and_skips_run_worker(monkeypatch, _migrated_engine):
    """Production run_once no longer enriches inline: it selects the due set (same
    selection/budget/TTL as the old batch) and ENQUEUES it onto public.pipeline_jobs
    in FIFO/taste order — capped by enrich_limit, TTL-filtered via claim_due — and
    never calls run_worker."""
    import agf.workers.main as m
    eng = _migrated_engine
    SENT = 999_100_000
    A1, A2, A3 = SENT + 1, SENT + 2, SENT + 3

    def _clean():
        with begin(eng) as conn:
            conn.execute(text("DELETE FROM public.pipeline_jobs WHERE advertiser_id >= :s"),
                         {"s": SENT})
            conn.execute(text("DELETE FROM logging.crawl_log WHERE entity_type='advertiser' "
                              "AND entity_id >= :s"), {"s": SENT})

    _clean()
    try:
        # A3 was crawled just now -> its TTL is not due -> claim_due must drop it.
        with begin(eng) as conn:
            conn.execute(text(
                "INSERT INTO logging.crawl_log (entity_type, entity_id, crawled_at, "
                "crawl_result) VALUES ('advertiser', :a, now(), 'ok')"), {"a": A3})

        # Isolate stage 3: skip the sweep + all optional stages, control the candidate set.
        monkeypatch.setattr(m, "discovery_due", lambda *a, **k: False)
        monkeypatch.setattr(m, "taste_prioritized_kept", lambda conn, **kw: [A1, A2, A3])
        monkeypatch.setattr(m, "eligible_gem_ids", lambda conn, s: [])

        def boom(*a, **k):
            raise AssertionError("production run_once must not call run_worker")

        monkeypatch.setattr(m, "run_worker", boom)

        summary = run_once(
            eng, object(), now_epoch=NOW, as_of=date(2026, 6, 4), dry_run=False,
            http_client=None, continuous_discovery=False,
            funnel_daily=False, corpus_refresh_daily=False,
            generic_deep_enabled=False, flywheel_daily=False,
            release_mint_enabled=False, media_repair_per_cycle=0,
            funnel_repair_per_cycle=0, enrich_budget=600)

        # A1, A2 never crawled -> due, in order; A3 filtered by TTL.
        assert summary["enqueued"] == 2
        assert summary["enriched"] == []
        assert summary["enrich_candidates"] == [A1, A2]
        with eng.connect() as conn:
            rows = conn.execute(text(
                "SELECT advertiser_id, status FROM public.pipeline_jobs "
                "WHERE advertiser_id >= :s ORDER BY id"), {"s": SENT}).fetchall()
        assert [r[0] for r in rows] == [A1, A2]        # FIFO / taste order preserved
        assert all(r[1] == "queued" for r in rows)
    finally:
        _clean()


def test_run_once_enqueues_due_keepers(monkeypatch, _migrated_engine):
    """Every production tick is the keeper PRODUCER: it selects due keepers and
    enqueues them as kind='keeper_history' pipeline_jobs in FIFO/taste order (the
    keeper daemon drains them). Mirrors the enrich-enqueue wiring test."""
    import agf.workers.main as m
    eng = _migrated_engine
    SENT = 999_200_000
    A1, A2 = SENT + 1, SENT + 2

    def _clean():
        with begin(eng) as conn:
            conn.execute(text("DELETE FROM public.pipeline_jobs WHERE advertiser_id >= :s"),
                         {"s": SENT})

    _clean()
    try:
        # Isolate the producer: skip the sweep + enrich; control the due keeper set.
        monkeypatch.setattr(m, "discovery_due", lambda *a, **k: False)
        monkeypatch.setattr(m, "keepers_due_for_history",
                            lambda conn, **kw: [(A1, "P1"), (A2, "P2")])
        monkeypatch.setattr(m, "taste_prioritized_kept", lambda conn, **kw: [])
        monkeypatch.setattr(m, "eligible_gem_ids", lambda conn, s: [])

        summary = run_once(
            eng, object(), now_epoch=NOW, as_of=date(2026, 6, 4), dry_run=False,
            http_client=None, continuous_discovery=False,
            funnel_daily=False, corpus_refresh_daily=False,
            generic_deep_enabled=False, flywheel_daily=False,
            release_mint_enabled=False, media_repair_per_cycle=0,
            funnel_repair_per_cycle=0, enrich_budget=600)

        assert summary["keeper_enqueued"] == 2
        with eng.connect() as conn:
            rows = conn.execute(text(
                "SELECT advertiser_id, kind, status FROM public.pipeline_jobs "
                "WHERE advertiser_id >= :s ORDER BY id"), {"s": SENT}).fetchall()
        assert [r[0] for r in rows] == [A1, A2]        # FIFO / taste order preserved
        assert all(r[1] == "keeper_history" and r[2] == "queued" for r in rows)
    finally:
        _clean()


def test_inline_keeper_pull_off_by_default(monkeypatch, _migrated_engine):
    """The old serial in-tick pull is opt-in: with a due sweep it does NOT run unless
    AGF_KEEPER_HISTORY_INLINE=1. Spies pull_keeper_history over a minimal production
    tick (sweep + enrich stubbed out)."""
    import agf.workers.main as m
    calls = []

    async def spy_pull(engine, extractor, **kw):
        calls.append(kw)
        return {"keepers_done": 0, "requests_spent": 0, "ads_stored": 0, "errors": 0}

    monkeypatch.setattr(m, "discovery_due", lambda *a, **k: True)   # sweep_due = True
    monkeypatch.setattr(m, "build_extractors", lambda dry_run, now_epoch, k: [object()])
    monkeypatch.setattr(m, "run_parallel_sweep", lambda *a, **k: {
        "keywords_swept": 0, "candidates_new": 0, "candidates_seen": 0,
        "errors": 0, "drops": {}})
    monkeypatch.setattr(m, "keepers_due_for_history", lambda conn, **kw: [])
    monkeypatch.setattr(m, "taste_prioritized_kept", lambda conn, **kw: [])
    monkeypatch.setattr(m, "eligible_gem_ids", lambda conn, s: [])
    monkeypatch.setattr(m, "pull_keeper_history", spy_pull)

    def _run():
        # http_client=None keeps triage + app-store stages offline (they gate on it).
        return run_once(
            _migrated_engine, object(), now_epoch=NOW, as_of=date(2026, 6, 4),
            dry_run=False, http_client=None, continuous_discovery=True,
            funnel_daily=False, corpus_refresh_daily=False,
            generic_deep_enabled=False, flywheel_daily=False,
            release_mint_enabled=False, media_repair_per_cycle=0,
            funnel_repair_per_cycle=0, enrich_budget=600)

    monkeypatch.delenv("AGF_KEEPER_HISTORY_INLINE", raising=False)
    summary = _run()
    assert calls == [] and summary["keeper_history"] is None

    monkeypatch.setenv("AGF_KEEPER_HISTORY_INLINE", "1")
    summary = _run()
    assert len(calls) == 1
    assert summary["keeper_history"] == {
        "keepers_done": 0, "requests_spent": 0, "ads_stored": 0, "errors": 0}


def test_keeper_enqueue_failure_does_not_abort_tick(monkeypatch, _migrated_engine):
    """A transient failure in the keeper-enqueue producer (keepers_due_for_history /
    enqueue_jobs) must NOT cost the app pipeline the rest of the tick — same isolation
    contract as the funnel/corpus stages. Make keepers_due_for_history raise, then assert
    run_once completes AND the later inline keeper-pull stage still ran."""
    import agf.workers.main as m
    calls = []

    async def spy_pull(engine, extractor, **kw):
        calls.append(kw)
        return {"keepers_done": 0, "requests_spent": 0, "ads_stored": 0, "errors": 0}

    def boom(conn, **kw):
        raise RuntimeError("transient DB error")

    monkeypatch.setattr(m, "discovery_due", lambda *a, **k: True)   # sweep_due = True
    monkeypatch.setattr(m, "build_extractors", lambda dry_run, now_epoch, k: [object()])
    monkeypatch.setattr(m, "run_parallel_sweep", lambda *a, **k: {
        "keywords_swept": 0, "candidates_new": 0, "candidates_seen": 0,
        "errors": 0, "drops": {}})
    monkeypatch.setattr(m, "keepers_due_for_history", boom)
    monkeypatch.setattr(m, "taste_prioritized_kept", lambda conn, **kw: [])
    monkeypatch.setattr(m, "eligible_gem_ids", lambda conn, s: [])
    monkeypatch.setattr(m, "pull_keeper_history", spy_pull)

    monkeypatch.setenv("AGF_KEEPER_HISTORY_INLINE", "1")  # exercise the later stage
    summary = run_once(
        _migrated_engine, object(), now_epoch=NOW, as_of=date(2026, 6, 4),
        dry_run=False, http_client=None, continuous_discovery=True,
        funnel_daily=False, corpus_refresh_daily=False,
        generic_deep_enabled=False, flywheel_daily=False,
        release_mint_enabled=False, media_repair_per_cycle=0,
        funnel_repair_per_cycle=0, enrich_budget=600)

    # tick completed despite the enqueue failure, and the later stage still ran
    assert summary["keeper_enqueued"] == 0
    assert len(calls) == 1


def test_dry_run_sweep_triages_and_enriches(monkeypatch, _migrated_engine):
    eng = _migrated_engine
    _clean_fake(eng)
    # keeper history is now enqueue-driven; the inline in-tick pull is opt-in. Flip it
    # on so this offline end-to-end still exercises pull_keeper_history (the daemon that
    # drains the enqueued jobs isn't running in-process here).
    monkeypatch.setenv("AGF_KEEPER_HISTORY_INLINE", "1")
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
               "AGF_DRY_RUN": "1", "AGF_KEEPER_HISTORY_INLINE": "1"}
        out = subprocess.run([sys.executable, "-m", "agf.workers.main"],
                             capture_output=True, text=True, env=env, timeout=120)
        assert out.returncode == 0, out.stderr
        assert "[worker] starting — mode=DRY-RUN" in out.stdout
        assert "kept=2" in out.stdout
        assert " hist=" in out.stdout
    finally:
        _clean_fake(_migrated_engine)


def test_flywheel_env_plumbing(monkeypatch):
    """The three new flywheel envs reach run_once's kwargs (defaults + overrides)."""
    from agf.workers import main as m
    cfg = m._cfg_from_env(continuous=False)
    assert cfg["flywheel_workers"] == 3            # spec defaults
    assert cfg["flywheel_retest_per_pass"] == 0    # retest lane off unless enabled
    assert cfg["flywheel_active_gap_hours"] == 12
    assert cfg["flywheel_mint_per_pass"] == 25
    monkeypatch.setenv("AGF_FLYWHEEL_WORKERS", "5")
    monkeypatch.setenv("AGF_FLYWHEEL_RETEST_PER_PASS", "7")
    monkeypatch.setenv("AGF_FLYWHEEL_ACTIVE_GAP_HOURS", "9")
    monkeypatch.setenv("AGF_FLYWHEEL_MINT_PER_PASS", "11")
    cfg = m._cfg_from_env(continuous=False)
    assert cfg["flywheel_workers"] == 5
    assert cfg["flywheel_retest_per_pass"] == 7
    assert cfg["flywheel_active_gap_hours"] == 9
    assert cfg["flywheel_mint_per_pass"] == 11
