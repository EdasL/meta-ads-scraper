import json

import pytest

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
            "source_term, was_new, triage, ad_evidence) VALUES (:r, :p, :n, 'ios', :s, 'sleep app', true, 'kept', CAST(:e AS jsonb)) "
            "RETURNING id"
        ), {"r": round_id, "p": f"pg{i}", "n": name, "s": f"id{i}", "e": json.dumps([{
            "ad_archive_id": str(i), "body_text": ["Help your baby fall asleep with soothing sounds.", "Play casino slots and win prizes."][i]}])}).scalar_one())
    return round_id, ids


def test_judge_round_marks_off_target(_migrated_engine, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    from agf.db.engine import begin  # match hunt.py's import path
    with begin(_migrated_engine) as conn:
        round_id, ids = _seed(conn)

    def fake_post(url, body):
        verdicts = [
            {"id": ids[0], "relevance": "relevant", "reason": "matches the ask"},
            {"id": ids[1], "relevance": "off_target", "reason": "casino game, not a sleep app"},
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
    assert left == 0  # NULL relevance everywhere = needs review
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
    assert left == 0  # nothing written = NULL relevance = needs review
    with begin(_migrated_engine) as conn:
        conn.execute(text("DELETE FROM frontend.hunt_results WHERE round_id = :r"), {"r": round_id})
        conn.execute(text("DELETE FROM frontend.hunt_rounds WHERE id = :r"), {"r": round_id})
        conn.execute(text("DELETE FROM frontend.hunts WHERE title = 'judge'"))


def test_judge_round_covers_competitors_and_vetoed_stores(_migrated_engine, monkeypatch):
    from agf.db.engine import begin
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    with begin(_migrated_engine) as conn:
        rid, ids = _seed(conn)
        conn.execute(text("UPDATE frontend.hunt_results SET source_term='page:AI suggestion', "
                          "triage='vetoed' WHERE id=:id"), {"id": ids[1]})
        conn.execute(text("INSERT INTO frontend.hunt_results "
                          "(round_id, page_id, page_name, store, store_id, source_term, was_new, triage) "
                          "SELECT round_id, page_id, page_name, 'web', 'example.test', 'other term', false, 'kept' "
                          "FROM frontend.hunt_results WHERE id=:id"), {"id": ids[1]})

    def fake_post(url, body):
        items = json.loads(body["contents"][0]["parts"][0]["text"].split("Advertisers (JSON):\n")[1])
        assert len(items) == 2  # the duplicate web/store rows are judged together
        return {"candidates": [{"content": {"parts": [{"text": json.dumps({"verdicts": [
            {"id": ids[0], "relevance": "relevant", "reason": "Helps new parents settle their baby."},
            {"id": ids[1], "relevance": "off_target", "reason": "Casino prizes do not solve sleep problems."},
        ]})}]}}]}

    try:
        assert judge_round(_migrated_engine, rid, "sleep apps for new parents", _post=fake_post) == {"judged": 3, "off_target": 2}
        with begin(_migrated_engine) as conn:
            vals = conn.execute(text("SELECT relevance FROM frontend.hunt_results WHERE round_id=:r AND page_id='pg1'"), {"r": rid}).scalars().all()
            assert vals == ["off_target", "off_target"]
    finally:
        with begin(_migrated_engine) as conn:
            conn.execute(text("DELETE FROM frontend.hunts WHERE id=(SELECT hunt_id FROM frontend.hunt_rounds WHERE id=:r)"), {"r": rid})


def test_missing_evidence_is_uncertain_without_model_call(_migrated_engine):
    from agf.db.engine import begin
    with begin(_migrated_engine) as conn:
        rid, _ = _seed(conn)
        conn.execute(text("UPDATE frontend.hunt_results SET ad_evidence='[]' WHERE round_id=:r"), {"r": rid})
    try:
        def no_post(*args):
            pytest.fail("Names alone must not be sent for judgment")
        judge_round(_migrated_engine, rid, "sleep apps", api_key="test", _post=no_post)
        with begin(_migrated_engine) as conn:
            assert set(conn.execute(text("SELECT relevance FROM frontend.hunt_results WHERE round_id=:r"), {"r": rid}).scalars()) == {"uncertain"}
    finally:
        with begin(_migrated_engine) as conn:
            conn.execute(text("DELETE FROM frontend.hunts WHERE id=(SELECT hunt_id FROM frontend.hunt_rounds WHERE id=:r)"), {"r": rid})


def test_judge_round_no_intent_skips(_migrated_engine):
    stats = judge_round(_migrated_engine, 999999, "")
    assert stats["judged"] == 0 and "skipped" in stats


@pytest.mark.parametrize('verdicts', [
    [{'id': 1, 'relevance': False, 'reason': 'missing evidence'}],
    [{'id': 1, 'relevant': False, 'reason': 'old schema'}],
    [{'id': True, 'relevance': 'off_target', 'reason': 'boolean id'}],
    [{'id': 1, 'relevance': 'off_target', 'reason': ''}],
    [{'id': 1, 'relevance': 'relevant', 'reason': 'yes'},
     {'id': 1, 'relevance': 'off_target', 'reason': 'no'}],
])
def test_invalid_or_conflicting_model_output_stays_unreviewed(verdicts):
    from agf.discovery.hunt_judge import _judge_batch
    def post(url, body):
        return {'candidates': [{'content': {'parts': [{'text': json.dumps({'verdicts': verdicts})}]}}]}
    assert _judge_batch([{'id': 1}], brief='ad angles', intent='screen time',
                        key='test', model='test', post=post) == {}


def test_user_brief_excludes_later_messages(_migrated_engine):
    from agf.discovery.hunt_judge import _round_brief
    from agf.db.engine import begin
    with begin(_migrated_engine) as conn:
        rid, _ = _seed(conn)
        conn.execute(text("INSERT INTO frontend.hunt_messages(hunt_id,role,content,created_at) "
                          "SELECT hunt_id,'user','Find ad angles for my app',created_at - interval '1 second' "
                          "FROM frontend.hunt_rounds WHERE id=:r"), {'r': rid})
        conn.execute(text("INSERT INTO frontend.hunt_messages(hunt_id,role,content,created_at) "
                          "SELECT hunt_id,'user','Unrelated later request',created_at + interval '1 second' "
                          "FROM frontend.hunt_rounds WHERE id=:r"), {'r': rid})
        try:
            assert _round_brief(conn, rid, 'narrow generated intent') == 'Find ad angles for my app'
        finally:
            conn.execute(text("DELETE FROM frontend.hunts WHERE id=(SELECT hunt_id FROM frontend.hunt_rounds WHERE id=:r)"), {'r': rid})
