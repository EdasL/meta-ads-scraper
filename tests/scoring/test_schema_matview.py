from sqlalchemy import text

FROZEN = {
    "advertiser_id", "cohort", "page_name", "app_id", "app_name", "gem_score",
    "scaling_velocity", "under_radar", "funnel_intent", "active_ad_count",
    "max_longevity_days", "alert", "refreshed_at",
}


def test_advertiser_scores_table_exists(pg_conn):
    cols = set(pg_conn.execute(
        text("SELECT * FROM adtech.advertiser_scores LIMIT 0")).keys())
    assert {"advertiser_id", "cohort", "gem_score", "alert", "vetoed",
            "veto_reason"} <= cols


def test_matview_exposes_frozen_columns(pg_conn):
    cols = set(pg_conn.execute(
        text("SELECT * FROM frontend.emerging_scaling_apps LIMIT 0")).keys())
    assert FROZEN <= cols, FROZEN - cols


def test_matview_has_unique_index_on_advertiser_id(pg_conn):
    defs = pg_conn.execute(text(
        "SELECT indexdef FROM pg_indexes WHERE schemaname='frontend' "
        "AND tablename='emerging_scaling_apps'")).scalars().all()
    assert any("UNIQUE" in d.upper() and "advertiser_id" in d for d in defs), defs
