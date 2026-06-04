from datetime import datetime, timezone
from sqlalchemy import text
from agf.workers.runner import run_worker


def test_run_worker_processes_each_due_once_and_logs(pg_conn, tmp_path):
    now = datetime.now(timezone.utc)
    seen = []

    def handler(conn, entity_id):
        seen.append(entity_id)
        return "ok"

    results = run_worker("testworker", "advertiser", handler, source=pg_conn,
                         candidate_ids=[10, 11, 12], due_before=now, limit=10,
                         mutex_dir=str(tmp_path))
    assert sorted(seen) == [10, 11, 12]                       # all never-crawled => due
    assert set(results) == {10, 11, 12}
    cnt = pg_conn.execute(text("SELECT count(*) FROM logging.crawl_log WHERE entity_type='advertiser' "
                               "AND entity_id IN (10, 11, 12)")).scalar()
    assert cnt == 3                                           # one crawl_log row each

    seen.clear()                                              # second pass: none due (just crawled)
    run_worker("testworker", "advertiser", handler, source=pg_conn,
               candidate_ids=[10, 11, 12], due_before=now, limit=10, mutex_dir=str(tmp_path))
    assert seen == []
