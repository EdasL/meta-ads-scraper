"""The worker's daily funnel stage — the gate (one finished funnel_sweep_runs row per
day), the run-log helpers, and run_funnel_stage end-to-end with an injected fake
extractor + resolve/probe (zero network). Skips cleanly without AGF_TEST_DB_URL."""
from datetime import date
from types import SimpleNamespace

from sqlalchemy import text

from agf.db.funnel_candidates import (close_funnel_sweep_run, funnel_sweep_ran_today,
                                      open_funnel_sweep_run)
from agf.workers.main import funnel_due, run_funnel_stage

TODAY = date(2026, 6, 4)


def test_funnel_gate_one_finished_run_per_day(pg_conn):
    # clean day: due (never in a dry-run — there is no offline render)
    assert funnel_due(pg_conn, TODAY, dry_run=False) is True
    assert funnel_due(pg_conn, TODAY, dry_run=True) is False
    # an OPEN (crashed, never finished) run does not block the day
    run_id = open_funnel_sweep_run(pg_conn)
    pg_conn.execute(text(
        "UPDATE discovery.funnel_sweep_runs SET started_at=:d WHERE id=:i"),
        {"d": TODAY, "i": run_id})
    assert funnel_due(pg_conn, TODAY, dry_run=False) is True
    # a FINISHED run gates the rest of the day
    close_funnel_sweep_run(pg_conn, run_id, stats={"sweep": {"keywords_swept": 1}})
    assert funnel_due(pg_conn, TODAY, dry_run=False) is False
    assert funnel_sweep_ran_today(pg_conn, TODAY) is True
    # ...but continuous mode (the 24/7 build-the-DB setting) runs every tick anyway,
    # and a dry-run never renders landing pages so it never runs.
    assert funnel_due(pg_conn, TODAY, dry_run=False, continuous=True) is True
    assert funnel_due(pg_conn, TODAY, dry_run=True, continuous=True) is False


def test_funnel_due_every_sec_throttles_a_recent_run(pg_conn, monkeypatch):
    monkeypatch.setenv("AGF_FUNNEL_EVERY_SEC", "3600")
    run_id = open_funnel_sweep_run(pg_conn)
    close_funnel_sweep_run(pg_conn, run_id, stats={})     # finished just now
    assert funnel_due(pg_conn, TODAY, dry_run=False) is False        # within the last hour
    pg_conn.execute(text("UPDATE discovery.funnel_sweep_runs "
                         "SET finished_at = now() - interval '2 hours' WHERE id=:i"),
                    {"i": run_id})
    assert funnel_due(pg_conn, TODAY, dry_run=False) is True         # interval elapsed


def test_close_run_records_stats_and_error(pg_conn):
    run_id = open_funnel_sweep_run(pg_conn)
    close_funnel_sweep_run(pg_conn, run_id, stats={"confirm": {"confirmed": 3}},
                           error="ExtractError('throttled')")
    row = pg_conn.execute(text(
        "SELECT finished_at, stats, error FROM discovery.funnel_sweep_runs "
        "WHERE id=:i"), {"i": run_id}).one()
    assert row.finished_at is not None
    assert row.stats == {"confirm": {"confirmed": 3}}
    assert row.error == "ExtractError('throttled')"


class _FakeAd:
    """Minimal AdRecord shape for classify_funnel_candidate."""
    def __init__(self, page_id, link_url):
        self.page_id = page_id
        self.page_name = f"Page {page_id}"
        self.page_like_count = 1200
        self.link_url = link_url
        self.cta_type = "LEARN_MORE"
        self.start_date = None
        self.body_text = "take the quiz"


class _FakeExtractor:
    """One web-funnel ad per searched keyword (quiz UTM => funnel signal)."""
    def __init__(self):
        self.searched = []

    async def search(self, *, query, **kw):
        self.searched.append(query)
        return [_FakeAd("FNL1", "https://fake-funnel-stage.example/quiz?utm_campaign=quiz")]


class _Resolved:
    kind = "ios"
    store_id = "999000111"


def test_run_funnel_stage_sweeps_and_confirms(_migrated_engine):
    eng = _migrated_engine

    def _clean():
        with eng.begin() as conn:
            conn.execute(text("DELETE FROM discovery.funnel_candidates "
                              "WHERE page_id = 'FNL1'"))
            # agf_it is a scratch DB — stray 'new' rows from other suites would eat
            # the confirm limit before FNL1 (same pattern as test_main._clean_fake).
            conn.execute(text("DELETE FROM discovery.funnel_candidates "
                              "WHERE status = 'new'"))
    _clean()
    try:
        from agf.discovery.taxonomy import all_entries
        entries = all_entries(layer="funnel_phrase")
        ex = _FakeExtractor()
        # run_funnel_stage sweeps the full funnel_phrase layer; the fake returns the
        # same ad for every keyword, so the upsert folds them into ONE candidate row.
        summary = run_funnel_stage(
            eng, ex, max_pages=1, kw_delay=0,
            confirm_limit=5,
            resolve=lambda url: _Resolved(),
            probe=lambda url: SimpleNamespace(
                landing_html="<html><script src='https://cdn.appsflyer.com/x.js'>"
                             "</script></html>",
                quiz_steps=0))
        assert summary["sweep"]["keywords_swept"] == len(entries)
        assert summary["sweep"]["candidates_new"] == 1
        # confirm labeled it install (resolved ios deeplink wins)
        with eng.connect() as conn:
            row = conn.execute(text(
                "SELECT kind, status, store, store_id FROM discovery.funnel_candidates "
                "WHERE page_id='FNL1'")).one()
        assert (row.kind, row.status) == ("install", "confirmed")
        assert (row.store, row.store_id) == ("ios", "999000111")
        assert summary["confirm"]["kinds"]["install"] >= 1
    finally:
        _clean()
