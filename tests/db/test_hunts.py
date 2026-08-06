"""frontend.hunt* repo tests (migration 155)."""
import json

import pytest
from sqlalchemy import text

from agf.db.hunts import (
    DEFAULT_HUNT_SETTINGS,
    apply_triage_to_results,
    clamp_hunt_settings,
    finish_round,
    load_round,
    mark_round_running,
    round_new_candidate_ids,
    update_round_progress,
    upsert_hunt_result,
)


def _seed_hunt(conn) -> int:
    return conn.execute(text(
        "INSERT INTO frontend.hunts (user_email, title) "
        "VALUES ('t@test.local', 'sleep stories') RETURNING id")).scalar_one()


def _seed_round(conn, hunt_id: int, terms=("sleep stories app",)) -> int:
    return conn.execute(text(
        "INSERT INTO frontend.hunt_rounds (hunt_id, terms, competitor_apps) "
        "VALUES (:h, CAST(:t AS jsonb), '[]') RETURNING id"),
        {"h": hunt_id, "t": json.dumps(list(terms))}).scalar_one()


def _result_row(round_id: int, **over) -> dict:
    row = {"round_id": round_id, "candidate_id": None, "page_id": "77001",
           "page_name": "Dreamy", "store": "ios", "store_id": "123456",
           "link_url": "https://apps.apple.com/us/app/id123456",
           "source_term": "sleep stories app", "was_new": True, "page": 1}
    row.update(over)
    return row


def test_load_round_returns_terms_and_competitors(pg_conn):
    hunt_id = _seed_hunt(pg_conn)
    round_id = _seed_round(pg_conn, hunt_id, terms=("a", "b"))
    row = load_round(pg_conn, round_id)
    assert row.id == round_id
    assert row.terms == ["a", "b"]
    assert row.competitor_apps == []
    assert row.status == "queued"
    assert row.settings == {}   # migration 156 default
    assert load_round(pg_conn, round_id + 999_000) is None


def test_load_round_returns_stored_settings(pg_conn):
    hunt_id = _seed_hunt(pg_conn)
    round_id = pg_conn.execute(text(
        "INSERT INTO frontend.hunt_rounds (hunt_id, terms, competitor_apps, settings) "
        "VALUES (:h, '[\"a\"]', '[]', CAST(:s AS jsonb)) RETURNING id"),
        {"h": hunt_id, "s": json.dumps({"page_cap": 12})}).scalar_one()
    assert load_round(pg_conn, round_id).settings == {"page_cap": 12}


def test_clamp_hunt_settings_defaults_and_bounds():
    assert clamp_hunt_settings(None) == DEFAULT_HUNT_SETTINGS
    assert clamp_hunt_settings({}) == DEFAULT_HUNT_SETTINGS
    # out-of-bounds clamp to the nearest legal value
    assert clamp_hunt_settings({"page_cap": 999})["page_cap"] == 100
    assert clamp_hunt_settings({"page_cap": 0})["page_cap"] == 1
    assert clamp_hunt_settings({"patience": 50})["patience"] == 10
    assert clamp_hunt_settings({"listing_pages": 99})["listing_pages"] == 30
    # float coerces to int within bounds
    assert clamp_hunt_settings({"page_cap": 12.9})["page_cap"] == 12
    # bad types fall back to the default (bool is NOT an int here, None, str)
    assert clamp_hunt_settings({"page_cap": True})["page_cap"] == 40
    assert clamp_hunt_settings({"page_cap": None})["page_cap"] == 40
    assert clamp_hunt_settings({"page_cap": "5"})["page_cap"] == 40
    assert clamp_hunt_settings({"min_pages": 99})["min_pages"] == 30
    assert clamp_hunt_settings({"min_pages": 0})["min_pages"] == 1
    # unknown keys ignored; a partial dict fills the rest from defaults
    assert clamp_hunt_settings({"patience": 5, "junk": 1}) == {
        "page_cap": 40, "patience": 5, "listing_pages": 10, "min_pages": 10,
        "skip_pages": 0}
    # skip_pages' floor is 0, not 1: reading from page 1 is a legal setting
    assert clamp_hunt_settings({"skip_pages": 99})["skip_pages"] == 30
    assert clamp_hunt_settings({"skip_pages": -1})["skip_pages"] == 0


def test_round_status_transitions(pg_conn):
    round_id = _seed_round(pg_conn, _seed_hunt(pg_conn))
    mark_round_running(pg_conn, round_id)
    assert load_round(pg_conn, round_id).status == "running"
    finish_round(pg_conn, round_id, status="error", error="boom")
    got = pg_conn.execute(text(
        "SELECT status, error_text, finished_at FROM frontend.hunt_rounds "
        "WHERE id = :i"), {"i": round_id}).one()
    assert got.status == "error" and got.error_text == "boom"
    assert got.finished_at is not None


def test_upsert_hunt_result_is_idempotent(pg_conn):
    round_id = _seed_round(pg_conn, _seed_hunt(pg_conn))
    upsert_hunt_result(pg_conn, _result_row(round_id))
    upsert_hunt_result(pg_conn, _result_row(round_id))  # same page+store+store_id
    n = pg_conn.execute(text(
        "SELECT count(*) FROM frontend.hunt_results WHERE round_id = :r"),
        {"r": round_id}).scalar_one()
    assert n == 1


def test_upsert_hunt_result_records_page(pg_conn):
    round_id = _seed_round(pg_conn, _seed_hunt(pg_conn))
    upsert_hunt_result(pg_conn, {
        "round_id": round_id, "candidate_id": None, "page_id": "p9",
        "page_name": "Deep Find", "store": "ios", "store_id": "id9",
        "link_url": None, "source_term": "running app", "was_new": True,
        "page": 7,
    })
    row = pg_conn.execute(text(
        "SELECT page FROM frontend.hunt_results "
        "WHERE round_id = :r AND page_id = 'p9'"), {"r": round_id}).one()
    assert row.page == 7


def test_update_round_progress_replaces_blob(pg_conn):
    round_id = _seed_round(pg_conn, _seed_hunt(pg_conn))
    update_round_progress(pg_conn, round_id, {"found": 3, "events": [
        {"t": "2026-07-13T12:00:00Z", "msg": "Searching 'a' — page 1"}]})
    got = pg_conn.execute(text(
        "SELECT progress FROM frontend.hunt_rounds WHERE id = :i"),
        {"i": round_id}).scalar_one()
    assert got["found"] == 3
    assert got["events"][0]["msg"].startswith("Searching")


def test_apply_triage_to_results_maps_candidate_status(pg_conn):
    round_id = _seed_round(pg_conn, _seed_hunt(pg_conn))
    # kept_advertiser_id is a FK to public.advertisers — seed the referenced row.
    pg_conn.execute(text(
        "INSERT INTO public.advertisers (id, page_id, page_name) "
        "VALUES (424242, 'HTESTADV', 'Kept Advertiser')"))
    kept_cid = pg_conn.execute(text(
        "INSERT INTO discovery.candidates (page_id, page_name, store, store_id, "
        "source_keyword, technique, status, kept_advertiser_id) "
        "VALUES ('HTEST1', 'Kept', 'ios', 'h1', 'x', 'hunt', 'kept', 424242) "
        "RETURNING id")).scalar_one()
    drop_cid = pg_conn.execute(text(
        "INSERT INTO discovery.candidates (page_id, page_name, store, store_id, "
        "source_keyword, technique, status, drop_reason) "
        "VALUES ('HTEST2', 'Dropped', 'android', 'h2', 'x', 'hunt', 'dropped', 'game') "
        "RETURNING id")).scalar_one()
    new_cid = pg_conn.execute(text(
        "INSERT INTO discovery.candidates (page_id, page_name, store, store_id, "
        "source_keyword, technique, status) "
        "VALUES ('HTEST3', 'Pending', 'ios', 'h3', 'x', 'hunt', 'new') "
        "RETURNING id")).scalar_one()
    for cid, page, store, sid in ((kept_cid, "HTEST1", "ios", "h1"),
                                  (drop_cid, "HTEST2", "android", "h2"),
                                  (new_cid, "HTEST3", "ios", "h3")):
        upsert_hunt_result(pg_conn, _result_row(
            round_id, candidate_id=cid, page_id=page, store=store, store_id=sid))
    stats = apply_triage_to_results(pg_conn, round_id)
    assert stats == {"kept": 1, "vetoed": 1, "pending": 1}
    got = {r.page_id: (r.triage, r.kept_advertiser_id) for r in pg_conn.execute(text(
        "SELECT page_id, triage, kept_advertiser_id FROM frontend.hunt_results "
        "WHERE round_id = :r"), {"r": round_id})}
    assert got["HTEST1"] == ("kept", 424242)
    assert got["HTEST2"] == ("vetoed", None)
    assert got["HTEST3"] == ("pending", None)
    assert round_new_candidate_ids(pg_conn, round_id) == sorted(
        [kept_cid, drop_cid, new_cid])
