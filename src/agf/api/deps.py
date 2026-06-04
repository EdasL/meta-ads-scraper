"""FastAPI connection dependencies (master-plan §6 P7-T3). GET routes get a
SET-TRANSACTION-READ-ONLY connection (default-deny writes); the single review POST
gets a writable connection that commits on success."""
from sqlalchemy import text


def get_conn(request):
    """Read-only connection: the transaction is marked READ ONLY, so any write
    (INSERT/UPDATE/DDL) is rejected by Postgres."""
    engine = request.app.state.engine
    conn = engine.connect()
    try:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        yield conn
    finally:
        conn.rollback()
        conn.close()


def get_write_conn(request):
    """Writable connection for the human review decision; commits on success."""
    engine = request.app.state.engine
    conn = engine.connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
