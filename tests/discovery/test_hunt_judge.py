import json

import pytest

from sqlalchemy import text

from agf.discovery.hunt_judge import judge_round


def _with_target(post):
    def reply(url, body):
        payload = json.loads(body["contents"][0]["parts"][0]["text"])
        if "brief" in payload:
            result = {"reference_mechanism": "soothing audio", "need": "Help a baby sleep",
                      "audience": "Parents settling their baby"}
            return {"candidates": [{"content": {"parts": [{"text": json.dumps(result)}]}}]}
        assert payload["target"] == {"need": "Help a baby sleep", "audience": "Parents settling their baby"}
        result = post(url, body)
        part = result["candidates"][0]["content"]["parts"][0]
        parsed = json.loads(part["text"])
        for v in parsed.get("verdicts") or []:
            rel = v.pop("relevance")
            v.update(clear_offer=rel != "uncertain", offers_need=rel == "relevant", audience_conflict=False)
        part["text"] = json.dumps(parsed)
        return result
    return reply


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

    stats = judge_round(_migrated_engine, round_id, "sleep apps for new parents", _post=_with_target(fake_post))
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

    stats = judge_round(_migrated_engine, round_id, "sleep apps for new parents", _post=_with_target(broken_post))
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

    stats = judge_round(_migrated_engine, round_id, "sleep apps for new parents", _post=_with_target(null_verdicts_post))
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
        items = json.loads(body["contents"][0]["parts"][0]["text"])["advertisers"]
        assert len(items) == 2  # the duplicate web/store rows are judged together
        return {"candidates": [{"content": {"parts": [{"text": json.dumps({"verdicts": [
            {"id": ids[0], "relevance": "relevant", "reason": "Helps new parents settle their baby."},
            {"id": ids[1], "relevance": "off_target", "reason": "Casino prizes do not solve sleep problems."},
        ]})}]}}]}

    try:
        assert judge_round(_migrated_engine, rid, "sleep apps for new parents", _post=_with_target(fake_post)) == {"judged": 3, "off_target": 2}
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


@pytest.mark.parametrize("use_product_id", [False, True])
def test_product_folder_reaches_target_and_respects_owner(_migrated_engine, use_product_id):
    from agf.db.engine import begin
    from agf.discovery.hunt_judge import _round_folder
    with begin(_migrated_engine) as conn:
        rid, ids = _seed(conn)
        fid = conn.execute(text("INSERT INTO frontend.saved_groups(user_email,name,position,product) "
                                "VALUES ('j@t.local','Selected brands',0,'My sleep product') RETURNING id")).scalar_one()
        foreign = conn.execute(text("INSERT INTO frontend.saved_groups(user_email,name,position,product) "
                                    "VALUES ('foreign@t.local','Private',0,'Private product') RETURNING id")).scalar_one()
        aid = conn.execute(text("INSERT INTO public.advertisers(page_id,page_name) "
                                "VALUES (:p,'Selected reference') RETURNING id"), {"p": f"folder-judge-{rid}"}).scalar_one()
        adid = conn.execute(text("INSERT INTO public.ads(external_ad_id,advertiser_id) VALUES (:p,:a) RETURNING id"),
                            {"p": f"folder-judge-{rid}", "a": aid}).scalar_one()
        conn.execute(text("INSERT INTO public.creatives(ad_id,type,body_text) VALUES (:a,'image','Help your baby sleep with calming sounds')"), {"a": adid})
        conn.execute(text("INSERT INTO frontend.saved_gems(user_email,advertiser_id,page_name,gem_score) "
                          "VALUES ('j@t.local',:a,'Selected reference',1)"), {"a": aid})
        conn.execute(text("INSERT INTO frontend.saved_group_members(user_email,group_id,advertiser_id) "
                          "VALUES ('j@t.local',:f,:a)"), {"f": fid, "a": aid})
        conn.execute(text("UPDATE frontend.hunts SET folder_id=:f WHERE id=(SELECT hunt_id FROM frontend.hunt_rounds WHERE id=:r)"), {"f": fid, "r": rid})
        pid = None
        if use_product_id:
            pid = conn.execute(text("INSERT INTO frontend.research_products(user_email,identity_key,name) "
                                    "VALUES ('j@t.local',:key,'My sleep product') RETURNING id"),
                               {"key": f"judge-product-{rid}"}).scalar_one()
            conn.execute(text("UPDATE frontend.saved_groups SET product_id=:p WHERE id=:f"), {"p": pid, "f": fid})
            conn.execute(text("UPDATE frontend.hunts SET product_id=:p WHERE id=(SELECT hunt_id FROM frontend.hunt_rounds WHERE id=:r)"), {"p": pid, "r": rid})

    saw_target = []
    def post(url, body):
        payload = json.loads(body["contents"][0]["parts"][0]["text"])
        if "brief" in payload:
            folder = payload["folder"]
            assert folder["product"].strip() == "My sleep product"
            if use_product_id:
                assert folder["reference_product"]["id"] == pid
            assert len(folder["saved_brands"]) == 1
            assert folder["saved_brands"][0]["ad_copy"] == ["Help your baby sleep with calming sounds"]
            assert "Private product" not in json.dumps(payload)
            saw_target.append(True)
            result = {"need": "Help a baby sleep", "audience": "Parents", "reference_mechanism": "sound"}
        else:
            result = {"verdicts": [{"id": i, "clear_offer": True, "offers_need": i == ids[0],
                                   "audience_conflict": False, "reason": "Checked against selected product"} for i in ids]}
        return {"candidates": [{"content": {"parts": [{"text": json.dumps(result)}]}}]}

    try:
        assert judge_round(_migrated_engine, rid, "sleep apps", api_key="test", _post=post)["judged"] == 2
        assert saw_target == [True]
        with begin(_migrated_engine) as conn:
            conn.execute(text("UPDATE frontend.hunts SET folder_id=:f WHERE id=(SELECT hunt_id FROM frontend.hunt_rounds WHERE id=:r)"), {"f": foreign, "r": rid})
            if use_product_id:
                assert _round_folder(conn, rid)["reference_product"]["id"] == pid
                conn.execute(text("UPDATE frontend.saved_groups SET product_id=NULL WHERE id=:f"), {"f": fid})
                assert _round_folder(conn, rid)["saved_brands"] == []
            else:
                assert _round_folder(conn, rid) is None
    finally:
        with begin(_migrated_engine) as conn:
            conn.execute(text("DELETE FROM frontend.hunts WHERE id=(SELECT hunt_id FROM frontend.hunt_rounds WHERE id=:r)"), {"r": rid})
            conn.execute(text("DELETE FROM frontend.saved_groups WHERE id IN (:f,:x)"), {"f": fid, "x": foreign})
            if pid:
                conn.execute(text("DELETE FROM frontend.research_products WHERE id=:p"), {"p": pid})
            conn.execute(text("DELETE FROM frontend.saved_gems WHERE advertiser_id=:a"), {"a": aid})
            conn.execute(text("DELETE FROM public.creatives WHERE ad_id=:a"), {"a": adid})
            conn.execute(text("DELETE FROM public.ads WHERE id=:a"), {"a": adid})
            conn.execute(text("DELETE FROM public.advertisers WHERE id=:a"), {"a": aid})


def test_model_batches_pack_evidence_and_preserve_missing_results(_migrated_engine, monkeypatch):
    from agf.db.engine import begin
    from agf.discovery import hunt_judge
    monkeypatch.setattr(hunt_judge, "_BATCH", 2)
    with begin(_migrated_engine) as conn:
        rid, _ = _seed(conn)
        for i in range(4):
            evidence = [{"body_text": "Help parents settle their baby to sleep."}] if i % 2 else []
            conn.execute(text(
                "INSERT INTO frontend.hunt_results "
                "(round_id,page_id,page_name,store,store_id,source_term,was_new,ad_evidence) "
                "VALUES (:r,:p,'Sleep app','web',:p,'sleep',true,CAST(:e AS jsonb))"
            ), {"r": rid, "p": f"batch-{i}", "e": json.dumps(evidence)})
    sizes = []

    def post(url, body):
        items = json.loads(body["contents"][0]["parts"][0]["text"])["advertisers"]
        sizes.append(len(items))
        assert all(item["ad_copy"] for item in items)
        return {"candidates": [{"content": {"parts": [{"text": json.dumps({"verdicts": [
            {"id": item["id"], "relevance": "relevant", "reason": "Supported offer."}
            for item in items
        ]})}]}}]}

    try:
        assert judge_round(_migrated_engine, rid, "sleep apps", api_key="test", _post=_with_target(post)) == {
            "judged": 6, "off_target": 0,
        }
        assert sizes == [2, 2]
        with begin(_migrated_engine) as conn:
            assert conn.execute(text(
                "SELECT count(*) FROM frontend.hunt_results WHERE round_id=:r AND relevance='uncertain'"
            ), {"r": rid}).scalar_one() == 2
    finally:
        with begin(_migrated_engine) as conn:
            conn.execute(text("DELETE FROM frontend.hunts WHERE id=(SELECT hunt_id FROM frontend.hunt_rounds WHERE id=:r)"), {"r": rid})


@pytest.mark.parametrize('verdicts', [
    [{'id': 1, 'clear_offer': 'yes', 'offers_need': True, 'audience_conflict': False, 'reason': 'bad flag'}],
    [{'id': 1, 'relevance': 'relevant', 'reason': 'old schema'}],
    [{'id': True, 'clear_offer': True, 'offers_need': True, 'audience_conflict': False, 'reason': 'boolean id'}],
    [{'id': 1, 'clear_offer': True, 'offers_need': True, 'audience_conflict': False, 'reason': ''}],
    [{'id': 1, 'clear_offer': True, 'offers_need': True, 'audience_conflict': False, 'reason': 'yes'},
     {'id': 1, 'clear_offer': True, 'offers_need': False, 'audience_conflict': False, 'reason': 'no'}],
    [{'id': 1, 'clear_offer': True, 'offers_need': True, 'audience_conflict': False, 'reason': 'yes'},
     {'id': 1, 'reason': 'malformed duplicate'}],
])
def test_invalid_or_conflicting_model_output_stays_unreviewed(verdicts):
    from agf.discovery.hunt_judge import _judge_batch
    def post(url, body):
        return {'candidates': [{'content': {'parts': [{'text': json.dumps({'verdicts': verdicts})}]}}]}
    assert _judge_batch([{'id': 1}], target={'need': 'sleep', 'audience': 'parents'},
                        key='test', model='test', post=post) == {}


@pytest.mark.parametrize('clear,need,conflict,expected', [
    (False, False, False, 'uncertain'),
    (True, False, False, 'off_target'),
    (True, True, True, 'off_target'),
    (True, True, False, 'relevant'),
])
def test_atomic_decisions_require_need_and_compatible_audience(clear, need, conflict, expected):
    from agf.discovery.hunt_judge import _judge_batch
    def post(url, body):
        result = {'verdicts': [{'id': 1, 'clear_offer': clear, 'offers_need': need,
                               'audience_conflict': conflict, 'reason': 'Observed offer.'}]}
        return {'candidates': [{'content': {'parts': [{'text': json.dumps(result)}]}}]}
    assert _judge_batch([{'id': 1}], target={'need': 'sleep', 'audience': 'parents'},
                        key='test', model='test', post=post) == {1: (expected, 'Observed offer.')}


def test_target_excludes_reference_mechanism():
    from agf.discovery.hunt_judge import _judge_target
    def post(url, body):
        assert json.loads(body['contents'][0]['parts'][0]['text']) == {'brief': 'original brief'}
        result = {'need': 'Control scrolling', 'audience': 'People managing their own habits',
                  'reference_mechanism': 'Answer quizzes to unlock apps'}
        return {'candidates': [{'content': {'parts': [{'text': json.dumps(result)}]}}]}
    assert _judge_target('original brief', key='test', model='test', post=post) == {
        'need': 'Control scrolling', 'audience': 'People managing their own habits',
    }


@pytest.mark.parametrize('target', [{}, {'need': '', 'audience': 'parents'},
                                    {'need': 42, 'audience': 'parents'},
                                    {'need': 'sleep', 'audience': 'x' * 801}])
def test_missing_or_malformed_target_stays_unreviewed(target):
    from agf.discovery.hunt_judge import _judge_target
    def post(url, body):
        return {'candidates': [{'content': {'parts': [{'text': json.dumps(target)}]}}]}
    assert _judge_target('brief', key='test', model='test', post=post) is None


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
