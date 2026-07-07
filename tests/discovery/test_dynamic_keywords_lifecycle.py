"""Migration 147 schema assertions: the 'resting' lifecycle columns + constraint, and
discovery.candidates.first_seen_depth. Behavior (cooldown gating, re-activation) is a
later task; T1 only proves the schema exists on the migrated engine."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import text


def test_dynamic_keyword_can_rest(pg_conn):
    # status='resting' must satisfy the extended CHECK, and next_eligible_at + rest_count
    # must be writable. A row that FAILS to insert here means 147 did not apply.
    eligible = datetime.now(timezone.utc) + timedelta(hours=6)
    pg_conn.execute(text(
        "INSERT INTO discovery.dynamic_keywords "
        "(id, keyword, source, kind, status, next_eligible_at, rest_count) VALUES "
        "(94701, 'lifecycle rest one', 'gem_copy', 'app', 'resting', :elig, 2)"),
        {"elig": eligible})
    row = pg_conn.execute(text(
        "SELECT status, next_eligible_at, rest_count FROM discovery.dynamic_keywords "
        "WHERE id = 94701")).one()
    assert row.status == "resting"
    assert row.rest_count == 2
    assert row.next_eligible_at is not None


def test_candidates_accepts_first_seen_depth(pg_conn):
    pg_conn.execute(text(
        "INSERT INTO discovery.candidates "
        "(id, page_id, store, store_id, source_keyword, first_seen_depth) VALUES "
        "(94702, 'LIFECYCLETEST1', 'ios', '94702', 'lifecycle depth kw', 3)"))
    depth = pg_conn.execute(text(
        "SELECT first_seen_depth FROM discovery.candidates WHERE id = 94702")).scalar_one()
    assert depth == 3
