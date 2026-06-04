from sqlalchemy import inspect


def test_metric_composite_pks(pg_conn):
    insp = inspect(pg_conn)
    assert insp.has_table("ad_metrics", schema="adtech")
    assert set(insp.get_pk_constraint("ad_metrics", schema="adtech")["constrained_columns"]) == {
        "ad_id", "snapshot_date"}
    assert insp.has_table("app_advertiser_metrics", schema="adtech")
    assert set(insp.get_pk_constraint("app_advertiser_metrics", schema="adtech")["constrained_columns"]) == {
        "advertiser_id", "week_start"}


def test_crawl_log_index(pg_conn):
    insp = inspect(pg_conn)
    assert insp.has_table("crawl_log", schema="logging")
    idx = insp.get_indexes("crawl_log", schema="logging")
    assert any(i["column_names"][:3] == ["entity_type", "entity_id", "crawled_at"] for i in idx), idx
