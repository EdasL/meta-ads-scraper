from sqlalchemy import text


def test_select_one(pg_conn):
    assert pg_conn.execute(text("SELECT 1")).scalar() == 1
