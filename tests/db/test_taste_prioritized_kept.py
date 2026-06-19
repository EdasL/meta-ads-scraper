# tests/db/test_taste_prioritized_kept.py
import os
import pytest
from sqlalchemy import text
from agf.db.engine import get_engine
from agf.db.migrate import apply_migrations
from agf.db.candidates import taste_prioritized_kept, kept_advertiser_ids

pytestmark = pytest.mark.skipif(
    not os.environ.get("AGF_TEST_DB_URL"), reason="needs AGF_TEST_DB_URL")

def test_falls_back_to_fifo_when_user_has_no_likes():
    eng = get_engine(os.environ["AGF_TEST_DB_URL"])
    apply_migrations(eng)
    with eng.begin() as conn:
        fifo = kept_advertiser_ids(conn)
        out = taste_prioritized_kept(conn, user="nobody_tp@test.local", explore_frac=0.25)
        assert out == fifo                       # cold-start user -> identical FIFO
