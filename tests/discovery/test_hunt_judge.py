"""LLM relevance judge for hunt rounds (schema 157).

Fail-open by design: any judge error leaves relevance NULL, which every reader
treats as relevant. Network runs outside any DB txn.
"""
import json

from sqlalchemy import text

from agf.discovery.hunt_judge import judge_round


def _seed(conn):
    hunt_id = conn.execute(text(
        "INSERT INTO frontend.hunts (user_email, title) VALUES ('j@t.local', 'judge') RETURNING id"
    )).scalar_one()
    round_id = conn.execute(text(
        "INSERT INTO frontend.hunt_rounds (hunt_id, terms, intent) "
        "VALUES (:h, '[\"sleep app\"]', 'sleep apps for new parents') RETURNING id"
    ), {"h": hunt_id}).scalar_one()
    ids = []
    for i, name in enumerate(["Baby Sleep Sounds", "Casino Slots Party"]):
        ids.append(conn.execute(text(
            "INSERT INTO frontend.hunt_results (round_id, page_id, page_name, store, store_id, "
            "source_term, was_new, triage) VALUES (:r, :p, :n, 'ios', :s, 'sleep app', true, 'kept') "
            "RETURNING id"
        ), {"r": round_id, "p": f"pg{i}", "n": name, "s": f"id{i}"}).scalar_one())
    return round_id, ids


def test_judge_round_marks_off_target(_migrated_engine, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    from agf.db.engine import begin  # match hunt.py's import path
    with begin(_migrated_engine) as conn:
        round_id, ids = _seed(conn)

    def fake_post(url, body):
        verdicts = [
            {"id": ids[0], "relevant": True, "reason": "matches the ask"},
            {"id": ids[1], "relevant": False, "reason": "casino game, not a sleep app"},
        ]
        return {"candidates": [{"content": {"parts": [{"text": json.dumps({"verdicts": verdicts})}]}}]}

    stats = judge_round(_migrated_engine, round_id, "sleep apps for new parents", _post=fake_post)
    assert stats == {"judged": 2, "off_target": 1}
    with begin(_migrated_engine) as conn:
        rows = dict(conn.execute(text(
            "SELECT id, relevance FROM frontend.hunt_results WHERE round_id = :r"), {"r": round_id}).fetchall())
    assert rows[ids[0]] == "relevant"
    assert rows[ids[1]] == "off_target"
    # cleanup
    with begin(_migrated_engine) as conn:
        conn.execute(text("DELETE FROM frontend.hunt_results WHERE round_id = :r"), {"r": round_id})
        conn.execute(text("DELETE FROM frontend.hunt_rounds WHERE id = :r"), {"r": round_id})
        conn.execute(text("DELETE FROM frontend.hunts WHERE title = 'judge'"))


def test_judge_round_fails_open(_migrated_engine, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    from agf.db.engine import begin
    with begin(_migrated_engine) as conn:
        round_id, ids = _seed(conn)

    def broken_post(url, body):
        raise KeyError("boom")

    stats = judge_round(_migrated_engine, round_id, "sleep apps for new parents", _post=broken_post)
    assert stats["judged"] == 0
    with begin(_migrated_engine) as conn:
        left = conn.execute(text(
            "SELECT count(*) FROM frontend.hunt_results WHERE round_id = :r AND relevance IS NOT NULL"),
            {"r": round_id}).scalar_one()
    assert left == 0  # NULL relevance everywhere = treated as relevant
    with begin(_migrated_engine) as conn:
        conn.execute(text("DELETE FROM frontend.hunt_results WHERE round_id = :r"), {"r": round_id})
        conn.execute(text("DELETE FROM frontend.hunt_rounds WHERE id = :r"), {"r": round_id})
        conn.execute(text("DELETE FROM frontend.hunts WHERE title = 'judge'"))


def test_judge_round_null_verdicts_fails_open(_migrated_engine, monkeypatch):
    """Valid JSON that violates the schema (verdicts not a list) must not crash the round."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    from agf.db.engine import begin
    with begin(_migrated_engine) as conn:
        round_id, ids = _seed(conn)

    def null_verdicts_post(url, body):
        return {"candidates": [{"content": {"parts": [{"text": json.dumps({"verdicts": None})}]}}]}

    stats = judge_round(_migrated_engine, round_id, "sleep apps for new parents", _post=null_verdicts_post)
    assert stats["judged"] == 0
    with begin(_migrated_engine) as conn:
        left = conn.execute(text(
            "SELECT count(*) FROM frontend.hunt_results WHERE round_id = :r AND relevance IS NOT NULL"),
            {"r": round_id}).scalar_one()
    assert left == 0  # nothing written = NULL relevance = treated as relevant
    with begin(_migrated_engine) as conn:
        conn.execute(text("DELETE FROM frontend.hunt_results WHERE round_id = :r"), {"r": round_id})
        conn.execute(text("DELETE FROM frontend.hunt_rounds WHERE id = :r"), {"r": round_id})
        conn.execute(text("DELETE FROM frontend.hunts WHERE title = 'judge'"))


def test_judge_round_no_intent_skips(_migrated_engine):
    stats = judge_round(_migrated_engine, 999999, "")
    assert stats["judged"] == 0 and "skipped" in stats
