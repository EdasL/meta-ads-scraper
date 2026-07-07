"""discovery.funnel_candidates repo — dedup bumps on (page_id, landing_domain), distinct
domain = new row, shared-domain clustering. Rolled-back pg_conn; FUNTEST* sentinels only."""
from datetime import date

from sqlalchemy import text

from agf.db import funnel_candidates as fcand
from agf.db.funnel_candidates import (close_funnel_sweep_run,
                                      funnel_deep_ran_within_days,
                                      open_funnel_sweep_run)

ROW = dict(page_id="FUNTEST1", page_name="Funnel Test", page_like_count=900,
           landing_url="https://quiz.funtest.world/start?utm_source=fb",
           landing_domain="quiz.funtest.world", funnel_host="quiz.",
           cta_type="LEARN_MORE", utm_source="fb", utm_medium=None,
           utm_campaign=None, utm_content=None, ad_start_date=date(2026, 6, 1),
           source_keyword="take the quiz", technique="funnel_phrase")


def _mine(rows):
    return [r for r in rows if r.page_id.startswith("FUNTEST")]


def test_upsert_inserts_then_bumps(pg_conn):
    cid, was_new = fcand.upsert_funnel_candidate(pg_conn, ROW)
    assert was_new is True
    cid2, was_new2 = fcand.upsert_funnel_candidate(pg_conn, {**ROW, "page_like_count": 1500})
    assert cid2 == cid and was_new2 is False
    row = _mine(fcand.new_funnel_candidates(pg_conn))[0]
    assert row.times_seen == 2
    assert row.page_like_count == 1500          # freshest non-null likes win


def test_distinct_domain_is_a_new_row(pg_conn):
    fcand.upsert_funnel_candidate(pg_conn, ROW)
    _, was_new = fcand.upsert_funnel_candidate(pg_conn, {**ROW, "landing_domain": "quiz2.funtest.world"})
    assert was_new is True


def test_cluster_by_domain(pg_conn):
    fcand.upsert_funnel_candidate(pg_conn, {**ROW, "page_id": "FUNTESTA"})
    fcand.upsert_funnel_candidate(pg_conn, {**ROW, "page_id": "FUNTESTB"})
    clusters = [c for c in fcand.cluster_by_domain(pg_conn, min_advertisers=2)
                if c.landing_domain == "quiz.funtest.world"]
    assert clusters and clusters[0].n_advertisers >= 2


def test_mark_funnel_confirmed(pg_conn):
    cid, _ = fcand.upsert_funnel_candidate(pg_conn, {**ROW, "page_id": "FUNTESTC"})
    fcand.mark_funnel_confirmed(pg_conn, cid, kind="install", status="confirmed",
                                store="ios", store_id="555")
    row = pg_conn.execute(text(
        "SELECT kind, status, store, store_id, confirmed_at "
        "FROM discovery.funnel_candidates WHERE id=:i"), {"i": cid}).one()
    assert row.kind == "install" and row.status == "confirmed"
    assert row.store == "ios" and row.store_id == "555" and row.confirmed_at is not None


def test_funnel_deep_ran_within_days(pg_conn):
    assert funnel_deep_ran_within_days(pg_conn, 7) is False
    rid = open_funnel_sweep_run(pg_conn)
    close_funnel_sweep_run(pg_conn, rid, stats={"sweep": {}, "deep": None, "confirm": {}})
    assert funnel_deep_ran_within_days(pg_conn, 7) is False      # JSON null != deep ran
    rid2 = open_funnel_sweep_run(pg_conn)
    close_funnel_sweep_run(pg_conn, rid2,
                           stats={"sweep": {}, "deep": {"page_log": []}, "confirm": {}})
    assert funnel_deep_ran_within_days(pg_conn, 7) is True
