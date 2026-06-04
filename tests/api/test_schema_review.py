import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from agf.parse import AdRecord
from agf.db import repo


def test_review_decisions_table_and_check(pg_conn):
    cols = set(pg_conn.execute(text(
        "SELECT * FROM frontend.review_decisions LIMIT 0")).keys())
    assert {"advertiser_id", "verdict", "reviewer", "note", "decided_at"} <= cols
    adv = repo.upsert_advertiser(pg_conn, AdRecord("RVW", "RVW", "G", True, 1, None, 1,
                                                   "x", "x", "https://x", "b"))
    with pytest.raises(IntegrityError):
        pg_conn.execute(text("INSERT INTO frontend.review_decisions "
                             "(advertiser_id, verdict, reviewer) VALUES (:a,'maybe','me')"),
                        {"a": adv})
