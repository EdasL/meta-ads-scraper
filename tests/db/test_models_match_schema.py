import pytest
from sqlalchemy import inspect, text
from agf.db.models import metadata
from agf.db.engine import begin


def test_models_match_db_columns(pg_conn):
    insp = inspect(pg_conn)
    for table in metadata.tables.values():
        schema = table.schema or "public"
        db_cols = {c["name"] for c in insp.get_columns(table.name, schema=schema)}
        model_cols = {c.name for c in table.columns}
        missing = model_cols - db_cols
        assert not missing, f"{schema}.{table.name} model columns missing from DB: {missing}"


def test_begin_commits_and_rolls_back(_migrated_engine):
    marker = "begin-commit.example"
    with begin(_migrated_engine) as conn:
        conn.execute(text("INSERT INTO public.domains(domain) VALUES (:d)"), {"d": marker})
    with _migrated_engine.connect() as c:           # committed -> visible elsewhere
        assert c.execute(text("SELECT count(*) FROM public.domains WHERE domain=:d"),
                         {"d": marker}).scalar() == 1

    with pytest.raises(RuntimeError):
        with begin(_migrated_engine) as conn:
            conn.execute(text("INSERT INTO public.domains(domain) VALUES (:d)"),
                         {"d": "begin-rollback.example"})
            raise RuntimeError("boom")
    with _migrated_engine.connect() as c:           # rolled back -> not present
        assert c.execute(text("SELECT count(*) FROM public.domains WHERE domain=:d"),
                         {"d": "begin-rollback.example"}).scalar() == 0

    with begin(_migrated_engine) as conn:           # cleanup the committed marker
        conn.execute(text("DELETE FROM public.domains WHERE domain=:d"), {"d": marker})
