"""run_worker: mutex + scheduler + handler (master-plan §3.9).

`handler` has signature Callable[[Connection, int], str] — cross-cutting deps
(extractor, client, s3) reach it via an adapter closure (§3.9)."""
import tempfile
from pathlib import Path
from collections.abc import Callable
from sqlalchemy.engine import Engine
from agf.workers.mutex import PidFileMutex
from agf.workers.scheduler import claim_due, mark_crawled


def run_worker(name: str, entity_type: str, handler: Callable[..., str], *,
               source, candidate_ids, due_before, limit: int = 100,
               mutex_dir: str | None = None) -> dict[int, str]:
    """Under a single-instance PID mutex, claim due entities and process each once
    (handler + mark_crawled committed together). `source` is an Engine (production:
    one transaction per entity) or a Connection (tests: a SAVEPOINT per entity).
    Returns {entity_id: handler_result}."""
    mdir = Path(mutex_dir or tempfile.gettempdir()) / "agf-mutex"
    results: dict[int, str] = {}
    with PidFileMutex(mdir / f"{name}.pid"):
        if isinstance(source, Engine):
            with source.connect() as conn:
                due = claim_due(conn, entity_type, candidate_ids, due_before, limit)
            for eid in due:
                try:
                    with source.begin() as conn:
                        results[eid] = handler(conn, eid)
                        mark_crawled(conn, entity_type, eid, results[eid])
                except Exception as exc:
                    # One entity's failure (e.g. a Playwright timeout on a dead
                    # landing) must not abort the rest of the batch. Record the
                    # error against the entity so its TTL applies and it isn't
                    # re-claimed on the next tight cycle, then move on.
                    results[eid] = f"error: {type(exc).__name__}"
                    try:
                        with source.begin() as conn:
                            mark_crawled(conn, entity_type, eid, results[eid])
                    except Exception:
                        pass
        else:  # Connection — run inside the caller's transaction via savepoints
            conn = source
            due = claim_due(conn, entity_type, candidate_ids, due_before, limit)
            for eid in due:
                with conn.begin_nested():
                    results[eid] = handler(conn, eid)
                    mark_crawled(conn, entity_type, eid, results[eid])
    return results
