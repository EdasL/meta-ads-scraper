"""discovery.candidates / sweep_runs repo semantics — dedup bumps, status never
regresses, daily-run gate. Rolled-back pg_conn; CANDTEST* sentinels only, and every
assertion filters to them (the shared agf_it DB may hold other suites' committed rows)."""
from datetime import date

from sqlalchemy import text

from agf.db import candidates as cand

ROW = dict(page_id="CANDTEST1", page_name="Cand Test", page_like_count=900,
           store="ios", store_id="999111", link_url="https://apps.apple.com/us/app/x/id999111",
           cta_type="DOWNLOAD", ad_start_date=date(2026, 6, 1),
           source_keyword="pelvic floor app", technique="niche_term")


def _mine(rows):
    return [r for r in rows if r.page_id.startswith("CANDTEST")]


def test_upsert_inserts_then_bumps(pg_conn):
    cid, was_new = cand.upsert_sweep_candidate(pg_conn, ROW)
    assert was_new is True
    cid2, was_new2 = cand.upsert_sweep_candidate(
        pg_conn, {**ROW, "page_like_count": 1200, "source_keyword": "other kw"})
    assert cid2 == cid and was_new2 is False
    row = _mine(cand.new_candidates(pg_conn))[0]
    assert row.times_seen == 2
    assert row.page_like_count == 1200          # freshest non-null likes win
    assert row.source_keyword == "pelvic floor app"   # first-seen provenance kept


def _depth(pg_conn, cid):
    return pg_conn.execute(text(
        "SELECT first_seen_depth FROM discovery.candidates WHERE id=:i"), {"i": cid}).scalar()


def test_first_seen_depth_keeps_shallowest_via_least(pg_conn):
    # 1) new row at depth 5
    cid, was_new = cand.upsert_sweep_candidate(pg_conn, {**ROW, "first_seen_depth": 5})
    assert was_new is True
    assert _depth(pg_conn, cid) == 5
    # 2) re-seen shallower (2) -> LEAST wins
    cid2, was_new2 = cand.upsert_sweep_candidate(pg_conn, {**ROW, "first_seen_depth": 2})
    assert cid2 == cid and was_new2 is False
    assert _depth(pg_conn, cid) == 2
    # 3) re-seen deeper (8) -> stays 2
    cand.upsert_sweep_candidate(pg_conn, {**ROW, "first_seen_depth": 8})
    assert _depth(pg_conn, cid) == 2


def test_first_seen_depth_null_semantics(pg_conn):
    # Insert with NO depth (key absent) -> inserts fine, depth NULL.
    row = dict(ROW, page_id="CANDTEST2", store_id="999222",
               link_url="https://apps.apple.com/us/app/x/id999222")
    cid, was_new = cand.upsert_sweep_candidate(pg_conn, row)
    assert was_new is True
    assert _depth(pg_conn, cid) is None
    # Re-seen WITH a depth -> LEAST(NULL, d) = d (Postgres LEAST ignores NULLs).
    cand.upsert_sweep_candidate(pg_conn, {**row, "first_seen_depth": 4})
    assert _depth(pg_conn, cid) == 4


def test_dropped_stays_dropped_on_reseen(pg_conn):
    cid, _ = cand.upsert_sweep_candidate(pg_conn, ROW)
    cand.mark_triaged(pg_conn, cid, status="dropped", drop_reason="game")
    cand.upsert_sweep_candidate(pg_conn, ROW)            # swept again next day
    assert _mine(cand.new_candidates(pg_conn)) == []     # not re-triaged
    got = pg_conn.execute(
        __import__("sqlalchemy").text(
            "SELECT status, drop_reason, times_seen FROM discovery.candidates WHERE id=:i"),
        {"i": cid}).one()
    assert got.status == "dropped" and got.drop_reason == "game" and got.times_seen == 2


def test_mark_kept_records_advertiser(pg_conn):
    from agf.db.repo import upsert_advertiser
    from types import SimpleNamespace
    cid, _ = cand.upsert_sweep_candidate(pg_conn, ROW)
    adv = upsert_advertiser(pg_conn, SimpleNamespace(
        page_id="CANDTEST1", page_name="Cand Test", page_like_count=900))
    cand.mark_triaged(pg_conn, cid, status="kept", kept_advertiser_id=adv)
    assert adv in cand.kept_advertiser_ids(pg_conn)
    assert _mine(cand.new_candidates(pg_conn)) == []


def test_sweep_run_gate(pg_conn):
    # open_sweep_run stamps started_at=now() (DB clock), so the gate must be checked
    # against the DB's date, not the host's — host date.today() vs DB now()::date can
    # differ by a day at the UTC/local midnight boundary (TZ skew → flaky failure).
    today = pg_conn.execute(text("SELECT now()::date")).scalar()
    assert cand.sweep_ran_today(pg_conn, today) is False
    run_id = cand.open_sweep_run(pg_conn)
    assert cand.sweep_ran_today(pg_conn, today) is False   # unfinished run doesn't gate
    cand.close_sweep_run(pg_conn, run_id,
                         sweep={"keywords_swept": 3, "serp_requests": 6,
                                "candidates_new": 2, "candidates_seen": 1,
                                "drops": {"social": 4}},
                         triage={"kept": 1, "dropped": {"game": 1}})
    assert cand.sweep_ran_today(pg_conn, today) is True


def test_reap_open_runs_closes_orphans_and_is_idempotent(pg_conn):
    # A run left open by a killed worker (no close_sweep_run) is an orphan.
    cand.open_sweep_run(pg_conn)
    cand.open_sweep_run(pg_conn)
    reaped = cand.reap_open_runs(pg_conn)
    assert reaped.get("sweep_runs", 0) >= 2   # >= : shared agf_it DB may hold other open rows
    # All sweep_runs rows now closed, stamped with the reaper marker.
    open_rows = pg_conn.execute(text(
        "SELECT count(*) FROM discovery.sweep_runs WHERE finished_at IS NULL")).scalar()
    assert open_rows == 0
    # Second call is a no-op (nothing still open).
    assert cand.reap_open_runs(pg_conn) == {}

