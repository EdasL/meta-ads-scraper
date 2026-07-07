"""Guard: every db/schema/*.sql must survive apply_migrations().

apply_migrations() runs each file via `conn.exec_driver_sql(path.read_text())`,
which hands the raw SQL to psycopg3. psycopg3 scans the *executable* SQL for
`%`-style parameter placeholders even when NO params are passed, so a lone `%`
in code (e.g. the modulo operator `id % 7`) raises `incomplete placeholder: '%'`
and the migration fails to apply — on a FRESH database only. A persistent test
DB that already recorded the migration silently skips re-execution, so ordinary
unit tests do NOT catch this; only a first-time prod apply does (bit us on 147's
stagger backfill: `make_interval(days => (id % 7)::int)`).

psycopg3's scanner correctly skips comments and string literals, so a `%` inside
a `-- comment`, `/* block */`, `'string'`, or `$$dollar-quoted$$` body is safe —
this test strips those first, then flags any `%` remaining in executable SQL.

Rule for migrations: never use the `%` operator — use `mod(a, b)` instead.
"""
import re
from pathlib import Path

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "db" / "schema"


def _strip_noncode(sql: str) -> str:
    """Remove the regions psycopg3's placeholder scanner ignores: block/line
    comments, single-quoted strings, and dollar-quoted bodies. What remains is
    executable SQL where a bare `%` would be read as a (broken) placeholder."""
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)      # /* block */
    sql = re.sub(r"--[^\n]*", " ", sql)                          # -- line
    sql = re.sub(r"\$(\w*)\$.*?\$\1\$", " ", sql, flags=re.DOTALL)  # $tag$ ... $tag$
    sql = re.sub(r"'(?:[^']|'')*'", " ", sql)                    # 'literal' ('' escape)
    return sql


def test_no_bare_percent_in_migration_code():
    offenders = [p.name for p in sorted(SCHEMA_DIR.glob("*.sql"))
                 if "%" in _strip_noncode(p.read_text())]
    assert not offenders, (
        "migration(s) contain a bare '%' in executable SQL that breaks "
        f"apply_migrations() on a fresh database (psycopg3 placeholder parsing): "
        f"{offenders}. Use mod(a, b) for modulo instead of the '%' operator."
    )
