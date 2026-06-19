import os
import pytest
from sqlalchemy import text
from agf.db.engine import get_engine
from agf.db.migrate import apply_migrations

pytestmark = pytest.mark.skipif(
    not os.environ.get("AGF_TEST_DB_URL"), reason="needs AGF_TEST_DB_URL")


def test_candidate_embeddings_shape_and_cascade():
    eng = get_engine(os.environ["AGF_TEST_DB_URL"])
    apply_migrations(eng)
    with eng.begin() as c:
        cols = {r[0] for r in c.execute(text(
            "SELECT attname FROM pg_attribute "
            "WHERE attrelid='discovery.candidate_embeddings'::regclass "
            "AND attnum>0 AND NOT attisdropped"))}
        assert {"candidate_id", "embedding", "content_hash", "dim",
                "model", "embedded_at"} <= cols
        # FK + CASCADE: insert a candidate + embedding, delete candidate, embedding gone
        cid = c.execute(text(
            "INSERT INTO discovery.candidates (page_id,page_name,store,store_id,"
            "source_keyword,technique,status) VALUES "
            "('M137','N','ios','9001','kw','niche','kept') RETURNING id")).scalar()
        c.execute(text(
            "INSERT INTO discovery.candidate_embeddings "
            "(candidate_id,embedding,content_hash,dim,model) "
            "VALUES (:i, ARRAY[0.1,0.2]::real[], 'h', 2, 't')"), {"i": cid})
        c.execute(text("DELETE FROM discovery.candidates WHERE id=:i"), {"i": cid})
        left = c.execute(text(
            "SELECT count(*) FROM discovery.candidate_embeddings WHERE candidate_id=:i"),
            {"i": cid}).scalar()
        assert left == 0
