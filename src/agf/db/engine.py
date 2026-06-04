import os
from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


def _normalize(url: str) -> str:
    """Force the psycopg (v3) driver for postgres URLs."""
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def get_engine(url: str | None = None) -> Engine:
    """Build a SQLAlchemy Engine. URL precedence: arg → AGF_DATABASE_URL → AGF_TEST_DB_URL."""
    url = url or os.environ.get("AGF_DATABASE_URL") or os.environ.get("AGF_TEST_DB_URL")
    if not url:
        raise RuntimeError("no database URL (set AGF_DATABASE_URL or pass url=)")
    return create_engine(_normalize(url), future=True, pool_pre_ping=True)


@contextmanager
def begin(engine: Engine | None = None):
    """Transactional connection: commits on success, rolls back on exception."""
    eng = engine or get_engine()
    with eng.begin() as conn:
        yield conn
