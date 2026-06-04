"""DB-as-queue staleness scheduler over logging.crawl_log."""
from sqlalchemy import text


def mark_crawled(conn, entity_type: str, entity_id: int, result: str = "ok") -> None:
    """Append a crawl-attempt row (sets crawled_at = now())."""
    # clock_timestamp() (not now()) — now() is frozen at transaction start, which would
    # back-date crawls done within one transaction and re-flag them as due.
    conn.execute(text("""
        INSERT INTO logging.crawl_log (entity_type, entity_id, crawled_at, crawl_result)
        VALUES (:etype, :eid, clock_timestamp(), :result)
    """), {"etype": entity_type, "eid": entity_id, "result": result})


def claim_due(conn, entity_type: str, candidate_ids, due_before, limit: int = 100) -> list[int]:
    """From `candidate_ids`, return those due for (re)crawl — never crawled, or whose
    latest crawl is older than `due_before` — oldest-first (never-crawled first)."""
    rows = conn.execute(text("""
        SELECT entity_id, max(crawled_at) AS last_crawled
        FROM logging.crawl_log WHERE entity_type = :etype
        GROUP BY entity_id
    """), {"etype": entity_type}).all()
    latest = {r[0]: r[1] for r in rows}
    due = [(latest.get(cid), cid) for cid in candidate_ids
           if latest.get(cid) is None or latest[cid] < due_before]
    # never-crawled (None) first, then oldest crawl first
    due.sort(key=lambda t: (t[0] is not None, t[0]))
    return [cid for _, cid in due[:limit]]
