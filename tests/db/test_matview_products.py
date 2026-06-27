import os
import pytest
from sqlalchemy import text
from agf.db.engine import get_engine
from agf.db.migrate import apply_migrations

DB = os.environ.get("AGF_TEST_DB_URL")
pytestmark = pytest.mark.skipif(not DB, reason="needs AGF_TEST_DB_URL")


def test_products_view_shape_and_conservation():
    eng = get_engine(DB)
    apply_migrations(eng)
    with eng.connect() as c:
        cols = {r[0] for r in c.execute(text(
            "SELECT attname FROM pg_attribute "
            "WHERE attrelid = 'frontend.emerging_scaling_products'::regclass "
            "AND attnum > 0 AND NOT attisdropped"))}
        # the two new columns plus the identity columns the feed reads
        assert {"page_count", "stores"} <= cols
        assert {"advertiser_id", "app_name", "icon_url", "gem_score",
                "active_ad_count", "store", "store_id", "funnel_url", "web_only"} <= cols

        prod_rows = c.execute(text(
            "SELECT count(*) FROM frontend.emerging_scaling_products")).scalar()
        mv_rows = c.execute(text(
            "SELECT count(*) FROM frontend.emerging_scaling_apps")).scalar()
        pages_sum = c.execute(text(
            "SELECT coalesce(sum(page_count), 0) FROM frontend.emerging_scaling_products")).scalar()

    # Dedup never adds rows, and every matview row belongs to exactly one product,
    # so the page_counts sum back to the matview row count.
    assert prod_rows <= mv_rows
    assert pages_sum == mv_rows
