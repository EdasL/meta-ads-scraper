"""The worker's recurring corpus-refresh stage — the cadence gate (one finished
corpus_refresh_runs row per day, or a within-seconds interval), the run-log helpers, and
the run_once slot end-to-end with refresh_corpus stubbed (zero Meta traffic). The stage
re-scrapes found advertisers' live listings on its OWN budget, isolated so a failure
lands on the run row and never aborts the tick. Skips cleanly without AGF_TEST_DB_URL."""
from datetime import date

from sqlalchemy import text

import agf.workers.main as wm
from agf.db.corpus_refresh_runs import (close_corpus_refresh_run,
                                        corpus_refresh_ran_today,
                                        corpus_refresh_ran_within,
                                        open_corpus_refresh_run)
from agf.workers.main import corpus_refresh_due, run_once

TODAY = date(2026, 6, 4)
NOW = 1_749_000_000


# --- cadence gate ------------------------------------------------------------
def test_corpus_gate_one_finished_run_per_day(pg_conn):
    # clean day: due (never in a dry-run — it needs real proxies + Meta traffic)
    assert corpus_refresh_due(pg_conn, TODAY, dry_run=False) is True
    assert corpus_refresh_due(pg_conn, TODAY, dry_run=True) is False
    # an OPEN (crashed, never finished) run does not block the day
    run_id = open_corpus_refresh_run(pg_conn)
    pg_conn.execute(text(
        "UPDATE discovery.corpus_refresh_runs SET started_at=:d WHERE id=:i"),
        {"d": TODAY, "i": run_id})
    assert corpus_refresh_due(pg_conn, TODAY, dry_run=False) is True
    # a FINISHED run gates the rest of the day
    close_corpus_refresh_run(pg_conn, run_id, stats={"advertisers_done": 3})
    assert corpus_refresh_due(pg_conn, TODAY, dry_run=False) is False
    assert corpus_refresh_ran_today(pg_conn, TODAY) is True
    # a dry-run never runs (no offline fake), even if it hasn't run today
    assert corpus_refresh_due(pg_conn, TODAY, dry_run=True, continuous=True) is False


def test_corpus_due_every_sec_throttles_a_recent_run(pg_conn, monkeypatch):
    monkeypatch.setenv("AGF_CORPUS_REFRESH_EVERY_SEC", "3600")
    run_id = open_corpus_refresh_run(pg_conn)
    close_corpus_refresh_run(pg_conn, run_id, stats={})       # finished just now
    assert corpus_refresh_ran_within(pg_conn, 3600) is True
    assert corpus_refresh_due(pg_conn, TODAY, dry_run=False) is False  # within the last hour
    pg_conn.execute(text("UPDATE discovery.corpus_refresh_runs "
                         "SET finished_at = now() - interval '2 hours' WHERE id=:i"),
                    {"i": run_id})
    assert corpus_refresh_ran_within(pg_conn, 3600) is False
    assert corpus_refresh_due(pg_conn, TODAY, dry_run=False) is True   # interval elapsed


def test_close_run_records_stats_and_error(pg_conn):
    run_id = open_corpus_refresh_run(pg_conn)
    close_corpus_refresh_run(pg_conn, run_id,
                             stats={"advertisers_done": 5, "flipped": 2},
                             error="ExtractError('throttled')")
    row = pg_conn.execute(text(
        "SELECT finished_at, stats, error FROM discovery.corpus_refresh_runs "
        "WHERE id=:i"), {"i": run_id}).one()
    assert row.finished_at is not None
    assert row.stats == {"advertisers_done": 5, "flipped": 2}
    assert row.error == "ExtractError('throttled')"


# --- the run_once slot -------------------------------------------------------
def _neutralize_neighbors(monkeypatch):
    """Isolate the corpus slot: skip the sweep/keeper stages (discovery_due=False), the
    enrich network (run_worker / prefetch stubbed), and the candidate-embed stage."""
    monkeypatch.setattr(wm, "discovery_due", lambda *a, **k: False)
    monkeypatch.setattr(wm, "prefetch_creative_urls", lambda *a, **k: {})
    monkeypatch.setattr(wm, "run_worker", lambda *a, **k: {})
    monkeypatch.setenv("AGF_TRIAGE_PRIOR", "0")


def _run_corpus_tick(engine, monkeypatch, *, refresh, budget):
    """Drive one run_once with only the corpus stage active; return the id-set of
    corpus_refresh_runs rows created during the tick (for isolated assertions)."""
    _neutralize_neighbors(monkeypatch)
    monkeypatch.setattr(wm, "refresh_corpus", refresh)
    with engine.connect() as conn:
        prev_max = conn.execute(text(
            "SELECT COALESCE(MAX(id), 0) FROM discovery.corpus_refresh_runs")).scalar()
    ext = wm.build_extractor(dry_run=True, now_epoch=NOW)      # unused by the stub
    run_once(engine, ext, now_epoch=NOW, as_of=TODAY, dry_run=False, http_client=None,
             funnel_daily=False, flywheel_daily=False, release_mint_enabled=False,
             generic_deep_enabled=False,
             score_after=False, media_repair_per_cycle=0, funnel_repair_per_cycle=0,
             corpus_refresh_daily=True, corpus_refresh_budget=budget)
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, finished_at, stats, error FROM discovery.corpus_refresh_runs "
            "WHERE id > :m ORDER BY id"), {"m": prev_max}).all()
    return prev_max, rows


def _cleanup(engine, prev_max):
    with wm.begin(engine) as conn:
        conn.execute(text("DELETE FROM discovery.corpus_refresh_runs WHERE id > :m"),
                     {"m": prev_max})


def test_stage_runs_when_due_and_records_stats_and_budget(_migrated_engine, monkeypatch):
    eng = _migrated_engine
    captured = {}
    stats = {"advertisers_done": 3, "requests_spent": 36, "ads_stored": 5,
             "flipped": 2, "truncated": 0, "errors": 0}

    async def _spy(engine, extractor, *, budget_requests, hist_pages):
        captured["budget"] = budget_requests
        captured["hist_pages"] = hist_pages
        return stats

    prev_max, rows = _run_corpus_tick(eng, monkeypatch, refresh=_spy, budget=1234)
    try:
        # the stage ran on its OWN budget (1234), NOT enrich's default (600), at hist_pages=12
        assert captured == {"budget": 1234, "hist_pages": 12}
        # exactly one FINISHED run row carrying the refresh stats, no error
        assert len(rows) == 1
        assert rows[0].finished_at is not None
        assert rows[0].stats == stats
        assert rows[0].error is None
    finally:
        _cleanup(eng, prev_max)


def test_stage_exception_lands_on_row_and_does_not_abort(_migrated_engine, monkeypatch):
    eng = _migrated_engine

    async def _boom(engine, extractor, *, budget_requests, hist_pages):
        raise RuntimeError("meta throttled")

    # run_once must return normally (the stage failure is isolated, never re-raised)
    prev_max, rows = _run_corpus_tick(eng, monkeypatch, refresh=_boom, budget=3000)
    try:
        assert len(rows) == 1
        assert rows[0].finished_at is not None          # the run row is CLOSED
        assert rows[0].stats is None
        assert "meta throttled" in rows[0].error        # the failure landed on the row
    finally:
        _cleanup(eng, prev_max)


def test_stage_skips_when_already_ran_today(_migrated_engine, monkeypatch):
    eng = _migrated_engine
    calls = {"n": 0}

    async def _spy(engine, extractor, *, budget_requests, hist_pages):
        calls["n"] += 1
        return {"advertisers_done": 0}

    # seed a FINISHED run for TODAY so the daily gate is already closed
    with wm.begin(eng) as conn:
        seed_id = open_corpus_refresh_run(conn)
        conn.execute(text("UPDATE discovery.corpus_refresh_runs "
                          "SET started_at=:d, finished_at=:d WHERE id=:i"),
                     {"d": TODAY, "i": seed_id})
    try:
        prev_max, rows = _run_corpus_tick(eng, monkeypatch, refresh=_spy, budget=3000)
        assert calls["n"] == 0            # refresh_corpus was NOT invoked
        assert rows == []                 # no new run row opened
    finally:
        with wm.begin(eng) as conn:
            conn.execute(text("DELETE FROM discovery.corpus_refresh_runs WHERE id=:i"),
                         {"i": seed_id})
