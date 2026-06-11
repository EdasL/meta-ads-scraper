from datetime import datetime, timezone, timedelta
from sqlalchemy import text
from agf.monitoring.metrics import compute_metrics, rotation_alerts, Alert


def _log(conn, etype, result, when):
    conn.execute(text("INSERT INTO logging.crawl_log (entity_type, entity_id, crawled_at, crawl_result) "
                      "VALUES (:t, 1, :w, :r)"), {"t": etype, "w": when, "r": result})


def test_metrics_and_rotation_alerts(pg_conn):
    now = datetime.now(timezone.utc)
    for _ in range(5):
        _log(pg_conn, "advertiser", "ok", now)
    for _ in range(4):
        _log(pg_conn, "advertiser", "error: HTTP 400 doc_id stale", now)
    _log(pg_conn, "advertiser", "error: 429 rate_limit", now)

    m = compute_metrics(pg_conn, window=timedelta(hours=1))
    assert m["total"] >= 10 and m["errors"] >= 5

    alerts = rotation_alerts(pg_conn, window=timedelta(hours=1),
                             doc_id_threshold=3, rate_limit_threshold=1)
    kinds = {a.kind for a in alerts}
    assert "doc_id_rotation" in kinds and "rate_limit" in kinds
    assert all(isinstance(a, Alert) for a in alerts)


def test_no_alerts_when_clean(pg_conn):
    # the shared scratch DB can hold other suites' committed error rows (the Go
    # integration seed leaves crawl_log populated) — clear inside this
    # rolled-back txn so "clean" is actually clean
    pg_conn.execute(text("DELETE FROM logging.crawl_log"))
    pg_conn.execute(text("INSERT INTO logging.crawl_log (entity_type, entity_id, crawl_result) "
                         "VALUES ('advertiser', 1, 'ok')"))
    assert rotation_alerts(pg_conn, doc_id_threshold=3, rate_limit_threshold=5) == []
