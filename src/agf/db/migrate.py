from pathlib import Path
from sqlalchemy.engine import Engine

# src/agf/db/migrate.py -> parents[3] == repo root
SCHEMA_DIR = Path(__file__).resolve().parents[3] / "db" / "schema"

_LEDGER = ("CREATE TABLE IF NOT EXISTS public.schema_migrations "
           "(filename TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())")


def apply_migrations(engine: Engine, schema_dir: Path = SCHEMA_DIR) -> list[str]:
    """Apply every db/schema/*.sql not yet recorded, in lexical order. Idempotent:
    a second run returns []. Returns the list of filenames applied this run."""
    applied: list[str] = []
    with engine.begin() as conn:
        conn.exec_driver_sql(_LEDGER)
        done = {r[0] for r in conn.exec_driver_sql("SELECT filename FROM public.schema_migrations")}
        for path in sorted(schema_dir.glob("*.sql")):
            if path.name in done:
                continue
            conn.exec_driver_sql(path.read_text())
            conn.exec_driver_sql(
                "INSERT INTO public.schema_migrations(filename) VALUES (%s)", (path.name,)
            )
            applied.append(path.name)
    return applied
