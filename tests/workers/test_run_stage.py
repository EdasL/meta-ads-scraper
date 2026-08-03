"""run_stage CLI: adapters call the SAME stage functions run_once uses, gates
bypassed, no run rows unless --record. Fakes only — zero network."""
import json

from sqlalchemy import text

from agf.workers import run_stage as rs


class _RecordingStage:
    def __init__(self, ret):
        self.calls, self.ret = [], ret

    def __call__(self, *a, **kw):
        self.calls.append((a, kw))
        return self.ret


def test_flywheel_adapter_scopes_and_passes_env_defaults(_migrated_engine, monkeypatch):
    spy = _RecordingStage({"keywords_swept": 1, "errors": 0})
    monkeypatch.setattr(rs, "run_flywheel_stage", spy)
    monkeypatch.setattr(rs, "build_extractors", lambda d, n, k: [object()] * k)
    monkeypatch.setattr(rs, "get_engine", lambda: _migrated_engine)
    monkeypatch.setenv("AGF_FLYWHEEL_MAX_TRIALS", "77")
    rc = rs.main(["flywheel", "--keywords", "a,b"])
    assert rc == 0
    (_, kw), = spy.calls
    assert kw["only_keywords"] == ["a", "b"]
    assert kw["max_trials"] == 77                  # env-true default
    assert kw["heartbeat"] is not None


def test_default_writes_no_run_row_and_record_writes_one(_migrated_engine, monkeypatch, capsys):
    eng = _migrated_engine
    spy = _RecordingStage({"keywords_swept": 0, "errors": 0, "mined": [], "retested": 0})
    monkeypatch.setattr(rs, "run_flywheel_stage", spy)
    monkeypatch.setattr(rs, "build_extractors", lambda d, n, k: [object()])
    monkeypatch.setattr(rs, "get_engine", lambda: eng)
    with eng.connect() as conn:
        before = conn.execute(text("SELECT count(*) FROM discovery.flywheel_runs")).scalar()
    assert rs.main(["flywheel"]) == 0              # default: test mode
    with eng.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM discovery.flywheel_runs")).scalar() == before
    assert rs.main(["flywheel", "--record"]) == 0  # --record: one finished row
    with eng.connect() as conn:
        row = conn.execute(text(
            "SELECT finished_at, stats FROM discovery.flywheel_runs "
            "ORDER BY id DESC LIMIT 1")).one()
    assert row.finished_at is not None and row.stats is not None
    out = capsys.readouterr().out.strip().splitlines()[-1]
    assert json.loads(out)["keywords_swept"] == 0  # stats JSON on stdout


def test_stage_exception_exits_nonzero_and_records_error_row(_migrated_engine, monkeypatch):
    eng = _migrated_engine

    def _boom(*a, **kw):
        raise RuntimeError("meta throttled")
    monkeypatch.setattr(rs, "run_flywheel_stage", _boom)
    monkeypatch.setattr(rs, "build_extractors", lambda d, n, k: [object()])
    monkeypatch.setattr(rs, "get_engine", lambda: eng)
    assert rs.main(["flywheel"]) == 1              # no row in test mode
    assert rs.main(["flywheel", "--record"]) == 1
    with eng.connect() as conn:
        row = conn.execute(text(
            "SELECT error FROM discovery.flywheel_runs ORDER BY id DESC LIMIT 1")).one()
    assert "meta throttled" in row.error           # error lands on the row


def test_unknown_stage_is_an_argparse_error():
    try:
        rs.main(["warp-drive"])
        raise AssertionError("should have raised SystemExit")
    except SystemExit as e:
        assert e.code == 2


# --- one spy test per remaining adapter (right fn, right kwargs) ---------------

def test_sweep_adapter_calls_parallel_sweep_no_triage(_migrated_engine, monkeypatch):
    spy = _RecordingStage({"candidates_new": 2})
    monkeypatch.setattr(rs, "run_parallel_sweep", spy)
    monkeypatch.setattr(rs, "build_extractors", lambda d, n, k: [object()] * k)
    monkeypatch.setattr(rs, "get_engine", lambda: _migrated_engine)
    assert rs.main(["sweep", "--no-triage", "--pages", "4"]) == 0
    (_, kw), = spy.calls
    assert kw["max_pages"] == 4 and kw["heartbeat"] is not None


def test_funnel_adapter_calls_funnel_stage_with_deep(_migrated_engine, monkeypatch):
    spy = _RecordingStage({"sweep": {}, "confirm": {}})
    monkeypatch.setattr(rs, "run_funnel_stage", spy)
    monkeypatch.setattr(rs, "build_extractor", lambda d, n: object())
    monkeypatch.setattr(rs, "get_engine", lambda: _migrated_engine)
    assert rs.main(["funnel", "--deep", "--pages", "9"]) == 0
    (_, kw), = spy.calls
    assert kw["deep"] is True and kw["max_pages"] == 9


def test_corpus_adapter_calls_refresh_corpus(_migrated_engine, monkeypatch):
    spy = _RecordingStage({"advertisers_done": 3})
    monkeypatch.setattr(rs, "refresh_corpus", spy)
    monkeypatch.setattr(rs, "build_extractor", lambda d, n: object())
    monkeypatch.setattr(rs, "get_engine", lambda: _migrated_engine)
    assert rs.main(["corpus", "--budget", "500", "--limit", "10"]) == 0
    (_, kw), = spy.calls
    assert kw["budget_requests"] == 500 and kw["limit"] == 10


def test_generic_deep_adapter_calls_stage(_migrated_engine, monkeypatch):
    spy = _RecordingStage({"minted": []})
    monkeypatch.setattr(rs, "run_generic_deep_stage", spy)
    monkeypatch.setattr(rs, "build_extractor", lambda d, n: object())
    monkeypatch.setattr(rs, "get_engine", lambda: _migrated_engine)
    assert rs.main(["generic-deep", "--pages", "120", "--mint-slots", "7"]) == 0
    (_, kw), = spy.calls
    assert kw["deep_pages"] == 120 and kw["mint_slots"] == 7


def test_mint_adapter_calls_frame_mint(_migrated_engine, monkeypatch):
    spy = _RecordingStage({"minted": 0})
    monkeypatch.setattr(rs, "mint_keywords_from_releases", spy)
    monkeypatch.setattr(rs, "get_engine", lambda: _migrated_engine)
    assert rs.main(["mint", "--slots", "12", "--no-autocomplete"]) == 0
    (_, kw), = spy.calls
    assert (kw["total_slots"] == 12 and kw["autocomplete"] is False
            and kw["new_release"] is False)


def test_topics_adapter_calls_refresh_topics(_migrated_engine, monkeypatch):
    spy = _RecordingStage({"rows": 12, "k": 3})
    monkeypatch.setattr(rs, "refresh_topics", spy)
    monkeypatch.setattr(rs, "get_engine", lambda: _migrated_engine)
    assert rs.main(["topics"]) == 0
    (a, _), = spy.calls
    assert a == (_migrated_engine,)


def test_triage_adapter_calls_run_triage(_migrated_engine, monkeypatch):
    spy = _RecordingStage({"kept": 0})
    monkeypatch.setattr(rs, "run_triage", spy)
    monkeypatch.setattr(rs, "get_engine", lambda: _migrated_engine)
    assert rs.main(["triage"]) == 0
    (_, kw), = spy.calls
    assert "lookup_ios" in kw and "lookup_android" in kw
