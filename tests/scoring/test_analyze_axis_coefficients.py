# tests/scoring/test_analyze_axis_coefficients.py
import os
import pytest
from sqlalchemy import text
from agf.db.engine import get_engine
from scripts.analyze_axis_coefficients import analyze

pytestmark = pytest.mark.skipif(
    not os.environ.get("AGF_TEST_DB_URL"), reason="needs AGF_TEST_DB_URL")


def test_analyze_returns_coefficients_without_writing(monkeypatch):
    eng = get_engine(os.environ["AGF_TEST_DB_URL"])
    with eng.begin() as conn:
        before = conn.execute(text("SELECT count(*) FROM frontend.preference_scores")).scalar()
        out = analyze(conn, "nobody@test.local")     # no labels -> heuristic, still returns
        assert "coefficients" in out and "version" in out
        after = conn.execute(text("SELECT count(*) FROM frontend.preference_scores")).scalar()
        assert before == after                        # analysis must not write serving rows
