"""Connection + safety layer for the scraping-health MCP.

- Reads the connection URL from AGF_DATABASE_URL (overridable per call, so tests
  can target the test DB on :55433).
- Every query runs in a READ ONLY session — a write attempt raises.
- Operational errors are classified into actionable one-liners instead of
  leaking stack traces to the model: proxy down -> try to (re)start the launchd
  cloud-sql-proxy and retry once; ADC token expired -> tell the user to re-auth.
"""

from __future__ import annotations

import os
import subprocess
import time
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError, DBAPIError

SQLPROXY_LABEL = "com.fielddesk.sqlproxy"
PROXY_PORT = "5433"


class DBError(Exception):
    """A connection/operational failure with an actionable message."""


def _normalize(url: str) -> str:
    # Force the psycopg (v3) driver regardless of how the URL is written.
    if url.startswith("postgresql+"):
        return url
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    return url


def _resolve_url(url: str | None) -> str:
    url = url or os.environ.get("AGF_DATABASE_URL")
    if not url:
        raise DBError("AGF_DATABASE_URL is not set.")
    return url


def _is_local_prod_proxy(url: str) -> bool:
    return PROXY_PORT in url and ("localhost" in url or "127.0.0.1" in url)


def _classify(exc: Exception, url: str) -> str:
    s = str(exc)
    low = s.lower()
    if any(k in low for k in ("invalid_rapt", "reauth", "invalid_grant", "could not fetch token")):
        return ("Google ADC token expired. Run: "
                "gcloud auth application-default login")
    if any(k in low for k in ("connection refused", "could not connect",
                              "closed the connection", "server terminated",
                              "connection reset")):
        hint = ("the cloud-sql-proxy may be down or ADC expired. Tried "
                "restarting it; if this persists run: "
                "gcloud auth application-default login"
                if _is_local_prod_proxy(url) else "the database is unreachable")
        return f"Cannot reach the database — {hint}."
    return f"Database error: {s[:300]}"


def _restart_proxy() -> None:
    try:
        subprocess.run(
            ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{SQLPROXY_LABEL}"],
            check=False, capture_output=True, timeout=15,
        )
        time.sleep(5)
    except Exception:
        pass  # best-effort; the retry will surface the real error if it failed


def get_engine(url: str | None = None):
    url = _normalize(_resolve_url(url))
    return create_engine(url, pool_pre_ping=True, future=True)


@contextmanager
def readonly_conn(url: str | None = None):
    """Yield a connection whose transactions are READ ONLY for its lifetime.

    default_transaction_read_only governs the *start* of a transaction, so we
    set it and commit to close the bootstrap transaction; every query issued
    after that begins a fresh, read-only transaction.
    """
    eng = get_engine(url)
    try:
        with eng.connect() as conn:
            conn.execute(text("SET default_transaction_read_only = on"))
            conn.commit()
            yield conn
    finally:
        eng.dispose()


def run_rows(sql: str, params: dict | None = None, url: str | None = None,
             _allow_retry: bool = True) -> list[dict]:
    """Run a SELECT and return rows as dicts. Read-only enforced."""
    resolved = _resolve_url(url)
    try:
        with readonly_conn(resolved) as conn:
            res = conn.execute(text(sql), params or {})
            cols = list(res.keys())
            return [dict(zip(cols, row)) for row in res.fetchall()]
    except (OperationalError, DBAPIError) as exc:
        if _allow_retry and _is_local_prod_proxy(_normalize(resolved)) \
                and "read-only" not in str(exc).lower():
            _restart_proxy()
            return run_rows(sql, params, url, _allow_retry=False)
        raise DBError(_classify(exc, resolved)) from exc
