"""Root conftest — the shared `pg_conn` fixture lives here ONLY (master-plan §3.10).
DB tests skip cleanly when AGF_TEST_DB_URL is unset."""
import os
import pytest

TEST_DB = os.environ.get("AGF_TEST_DB_URL")


@pytest.fixture(scope="session")
def _migrated_engine():
    if not TEST_DB:
        pytest.skip("AGF_TEST_DB_URL not set — DB tests skipped")
    from agf.db.engine import get_engine
    from agf.db.migrate import apply_migrations
    engine = get_engine(TEST_DB)
    apply_migrations(engine)
    return engine


@pytest.fixture
def pg_conn(_migrated_engine):
    """A connection wrapped in a transaction that is rolled back after each test."""
    conn = _migrated_engine.connect()
    txn = conn.begin()
    try:
        yield conn
    finally:
        txn.rollback()
        conn.close()
