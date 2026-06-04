from datetime import datetime, timezone, timedelta
from sqlalchemy import text
from agf.workers.scheduler import claim_due, mark_crawled


def test_claim_due_oldest_first_and_drops_after_mark(pg_conn):
    now = datetime.now(timezone.utc)
    mark_crawled(pg_conn, "advertiser", 1, "ok")               # entity 1: crawled just now
    pg_conn.execute(text("INSERT INTO logging.crawl_log(entity_type, entity_id, crawled_at, "
                         "crawl_result) VALUES ('advertiser', 2, :t, 'ok')"),
                    {"t": now - timedelta(days=10)})           # entity 2: crawled 10d ago
    # entity 3: never crawled

    due = claim_due(pg_conn, "advertiser", [1, 2, 3], due_before=now - timedelta(days=1), limit=10)
    assert due == [3, 2]                                       # never-crawled first, then oldest; 1 not due

    mark_crawled(pg_conn, "advertiser", 3, "ok")               # now 3 is fresh
    due2 = claim_due(pg_conn, "advertiser", [1, 2, 3], due_before=now - timedelta(days=1), limit=10)
    assert due2 == [2]                                         # 3 dropped, 2 still due


def test_claim_due_respects_limit(pg_conn):
    now = datetime.now(timezone.utc)
    due = claim_due(pg_conn, "advertiser", [10, 11, 12, 13], due_before=now, limit=2)
    assert len(due) == 2
