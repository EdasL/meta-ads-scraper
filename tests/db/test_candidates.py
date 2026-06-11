"""discovery.candidates / sweep_runs repo semantics — dedup bumps, status never
regresses, daily-run gate. Rolled-back pg_conn; CANDTEST* sentinels only, and every
assertion filters to them (the shared agf_it DB may hold other suites' committed rows)."""
from datetime import date

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
    # open_sweep_run stamps started_at=now(), so the gate must be checked
    # against the real current date — a pinned date breaks at midnight
    today = date.today()
    assert cand.sweep_ran_today(pg_conn, today) is False
    run_id = cand.open_sweep_run(pg_conn)
    assert cand.sweep_ran_today(pg_conn, today) is False   # unfinished run doesn't gate
    cand.close_sweep_run(pg_conn, run_id,
                         sweep={"keywords_swept": 3, "serp_requests": 6,
                                "candidates_new": 2, "candidates_seen": 1,
                                "drops": {"social": 4}},
                         triage={"kept": 1, "dropped": {"game": 1}})
    assert cand.sweep_ran_today(pg_conn, today) is True
