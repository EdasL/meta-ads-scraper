from fastapi.testclient import TestClient
from sqlalchemy import text
from agf.api.app import create_app
from agf.db.engine import begin


def test_health_metrics_endpoint(_migrated_engine):
    with begin(_migrated_engine) as conn:
        conn.execute(text("INSERT INTO logging.crawl_log (entity_type, entity_id, crawl_result) "
                          "VALUES ('healthcheck_sentinel', 1, 'ok')"))
    try:
        c = TestClient(create_app(_migrated_engine))
        body = c.get("/api/health/metrics").json()
        assert "total" in body and "errors" in body and "alerts" in body
        assert isinstance(body["alerts"], list) and "degraded" in body
    finally:
        with begin(_migrated_engine) as conn:
            conn.execute(text("DELETE FROM logging.crawl_log WHERE entity_type='healthcheck_sentinel'"))
