"""Schema presence for migration 161 (topic diversity)."""
from sqlalchemy import text


def _cols(conn, schema, table):
    return {r.column_name for r in conn.execute(text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = :s AND table_name = :t"),
        {"s": schema, "t": table})}


def test_app_topics_table(pg_conn):
    assert _cols(pg_conn, "frontend", "app_topics") >= {
        "advertiser_id", "topic_id", "topic_size", "computed_at"}


def test_keyword_suppressions_table(pg_conn):
    assert _cols(pg_conn, "discovery", "keyword_suppressions") >= {
        "keyword", "reason", "kept_n", "crowded_share", "suppressed_at"}
