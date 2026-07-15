"""Hunt daemon claim/safety-net tests (fake runner, real pipeline_jobs)."""
import json

import pytest
from sqlalchemy import text

from agf.db.engine import begin
from agf.db.pipeline_jobs import enqueue_jobs
from agf.enrich_full.enrich_one import set_status


def _mk_round(engine) -> int:
    with begin(engine) as conn:
        hunt_id = conn.execute(text(
            "INSERT INTO frontend.hunts (user_email, title) "
            "VALUES ('d@test.local', 'daemon-test') RETURNING id")).scalar_one()
        return conn.execute(text(
            "INSERT INTO frontend.hunt_rounds (hunt_id, terms) "
            "VALUES (:h, CAST(:t AS jsonb)) RETURNING id"),
            {"h": hunt_id, "t": json.dumps(["x"])}).scalar_one()


@pytest.fixture
def engine(_migrated_engine):
    def _clean():
        with begin(_migrated_engine) as conn:
            conn.execute(text("DELETE FROM public.pipeline_jobs WHERE kind = 'hunt'"))
            conn.execute(text(
                "DELETE FROM frontend.hunts WHERE user_email = 'd@test.local'"))
    _clean()
    try:
        yield _migrated_engine
    finally:
        _clean()


def _job_status(engine, round_id):
    with engine.connect() as conn:
        return conn.execute(text(
            "SELECT status FROM public.pipeline_jobs "
            "WHERE kind = 'hunt' AND advertiser_id = :r"),
            {"r": round_id}).scalar_one()


def test_process_once_claims_hunt_job(engine):
    round_id = _mk_round(engine)
    with begin(engine) as conn:
        enqueue_jobs(conn, "hunt", [round_id])
    calls = []
    def fake_runner(round_id_arg, job_id):
        calls.append((round_id_arg, job_id))
        set_status(engine, job_id, "done", finished=True,
                   status_table="pipeline_jobs")
    from agf.discovery.hunt_daemon import process_once
    assert process_once(engine, runner=fake_runner) is True
    assert len(calls) == 1 and calls[0][0] == round_id
    assert _job_status(engine, round_id) == "done"
    assert process_once(engine, runner=fake_runner) is False  # queue drained


def test_safety_net_marks_job_and_round_on_runner_death(engine):
    round_id = _mk_round(engine)
    with begin(engine) as conn:
        enqueue_jobs(conn, "hunt", [round_id])
    def dead_runner(round_id_arg, job_id):
        raise RuntimeError("subprocess killed")
    from agf.discovery.hunt_daemon import process_once
    assert process_once(engine, runner=dead_runner) is True
    assert _job_status(engine, round_id) == "error"
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT status, error_text FROM frontend.hunt_rounds WHERE id = :i"),
            {"i": round_id}).one()
    assert row.status == "error" and "killed" in row.error_text


class _FakeResp:
    def __init__(self, status_code):
        self.status_code = status_code


class _FakeClient:
    """Captures POST args; returns a canned response or raises."""
    def __init__(self, resp=None, exc=None):
        self._resp = resp
        self._exc = exc
        self.calls = []

    def post(self, url, headers=None, timeout=None):
        self.calls.append((url, headers, timeout))
        if self._exc is not None:
            raise self._exc
        return self._resp


def test_notify_completed_posts_with_token(monkeypatch):
    monkeypatch.setenv("AGF_API_URL", "http://api.test")
    monkeypatch.setenv("AGF_API_TOKEN", "tok")
    from agf.discovery.hunt_one import _notify_completed
    client = _FakeClient(resp=_FakeResp(200))
    assert _notify_completed(7, client) is True
    url, headers, _ = client.calls[0]
    assert url.endswith("/api/internal/hunts/rounds/7/completed")
    assert headers["Authorization"] == "Bearer tok"


def test_notify_completed_false_on_error_or_missing_env(monkeypatch):
    from agf.discovery.hunt_one import _notify_completed
    # (a) no env -> False, no HTTP call
    monkeypatch.delenv("AGF_API_URL", raising=False)
    monkeypatch.delenv("AGF_API_TOKEN", raising=False)
    unused = _FakeClient(resp=_FakeResp(200))
    assert _notify_completed(1, unused) is False
    assert unused.calls == []
    # (b) client raises -> False
    monkeypatch.setenv("AGF_API_URL", "http://api.test")
    monkeypatch.setenv("AGF_API_TOKEN", "tok")
    assert _notify_completed(2, _FakeClient(exc=RuntimeError("down"))) is False
    # (c) non-200 -> False
    assert _notify_completed(3, _FakeClient(resp=_FakeResp(503))) is False


def _mk_job(engine, round_id) -> int:
    with begin(engine) as conn:
        enqueue_jobs(conn, "hunt", [round_id])
    with engine.connect() as conn:
        return conn.execute(text(
            "SELECT id FROM public.pipeline_jobs "
            "WHERE kind = 'hunt' AND advertiser_id = :r"),
            {"r": round_id}).scalar_one()


def _assistant_msg_count(engine, round_id) -> int:
    with engine.connect() as conn:
        return conn.execute(text(
            "SELECT count(*) FROM frontend.hunt_messages m "
            "JOIN frontend.hunt_rounds r ON r.hunt_id = m.hunt_id "
            "WHERE r.id = :r AND m.role = 'assistant'"),
            {"r": round_id}).scalar_one()


def test_done_path_writes_canned_message_only_when_notify_fails(engine, monkeypatch):
    from agf.discovery import hunt_one
    monkeypatch.setattr(hunt_one, "run_hunt_round", lambda *a, **k: {"found": 0})
    monkeypatch.setattr(hunt_one, "_build_extractors", lambda k: [])

    # Hook fails -> hunt_one writes the canned fallback message.
    monkeypatch.setattr(hunt_one, "_notify_completed", lambda *a, **k: False)
    rid = _mk_round(engine)
    jid = _mk_job(engine, rid)
    assert hunt_one.main(["--round-id", str(rid), "--job-id", str(jid)]) == 0
    assert _job_status(engine, rid) == "done"
    assert _assistant_msg_count(engine, rid) == 1

    # Hook succeeds (Peon wrote the message) -> hunt_one writes NO message.
    monkeypatch.setattr(hunt_one, "_notify_completed", lambda *a, **k: True)
    rid2 = _mk_round(engine)
    jid2 = _mk_job(engine, rid2)
    assert hunt_one.main(["--round-id", str(rid2), "--job-id", str(jid2)]) == 0
    assert _job_status(engine, rid2) == "done"
    assert _assistant_msg_count(engine, rid2) == 0
