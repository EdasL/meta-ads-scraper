import pytest
from sqlalchemy import text
from agf.db.engine import begin, get_engine
from agf.enrich_full import enrich_one


def _new_job(conn, advertiser_id=999001):
    row = conn.execute(text(
        "INSERT INTO public.enrich_jobs (advertiser_id) VALUES (:a) RETURNING id"
    ), {"a": advertiser_id}).first()
    return row[0]


def _new_pipeline_job(conn, advertiser_id=999042):
    row = conn.execute(text(
        "INSERT INTO public.pipeline_jobs (kind, advertiser_id) "
        "VALUES ('enrich', :a) RETURNING id"
    ), {"a": advertiser_id}).first()
    return row[0]


def _status(conn, job_id):
    return conn.execute(text(
        "SELECT status, error_text, finished_at FROM public.enrich_jobs WHERE id=:i"
    ), {"i": job_id}).first()


def _pipeline_status(conn, job_id):
    return conn.execute(text(
        "SELECT status, error_text, finished_at FROM public.pipeline_jobs WHERE id=:i"
    ), {"i": job_id}).first()


def test_run_job_marks_done_on_success():
    engine = get_engine()
    calls = {}
    with begin(engine) as conn:
        job_id = _new_job(conn)

    def fake_enrich(engine_, advertiser_id, **kw):
        calls["enrich"] = advertiser_id

    def fake_score(engine_, advertiser_id, as_of):
        calls["score"] = (advertiser_id, as_of)

    enrich_one.run_job(engine, advertiser_id=999001, job_id=job_id,
                       enrich_call=fake_enrich, score_call=fake_score)

    with begin(engine) as conn:
        status, err, finished = _status(conn, job_id)
    assert status == "done"
    assert err is None
    assert finished is not None
    assert calls["enrich"] == 999001
    assert calls["score"][0] == 999001


def test_run_job_marks_error_on_exception():
    engine = get_engine()
    with begin(engine) as conn:
        job_id = _new_job(conn)

    def boom(engine_, advertiser_id, **kw):
        raise RuntimeError("render exploded")

    try:
        enrich_one.run_job(engine, advertiser_id=999001, job_id=job_id,
                           enrich_call=boom, score_call=lambda *a, **k: None)
    except RuntimeError:
        pass  # run_job re-raises after recording

    with begin(engine) as conn:
        status, err, finished = _status(conn, job_id)
    assert status == "error"
    assert "render exploded" in err
    assert finished is not None


def test_run_job_skips_score_when_enrich_fails():
    engine = get_engine()
    scored = []
    with begin(engine) as conn:
        job_id = _new_job(conn)

    def boom(engine_, advertiser_id, **kw):
        raise RuntimeError("nope")

    try:
        enrich_one.run_job(engine, advertiser_id=999001, job_id=job_id,
                           enrich_call=boom, score_call=lambda *a, **k: scored.append(a))
    except RuntimeError:
        pass
    assert scored == []  # score never runs if enrich raised


# --- Task 2: --status-table + --mark-crawled -------------------------------

def test_default_targets_enrich_jobs_and_no_mark_crawled(monkeypatch):
    """On-demand default: writes public.enrich_jobs, never touches crawl_log."""
    engine = get_engine()
    marks = []
    monkeypatch.setattr(enrich_one, "mark_crawled",
                        lambda conn, etype, eid: marks.append((etype, eid)))
    with begin(engine) as conn:
        job_id = _new_job(conn)

    enrich_one.run_job(engine, advertiser_id=999001, job_id=job_id,
                       enrich_call=lambda *a, **k: None,
                       score_call=lambda *a, **k: None)

    with begin(engine) as conn:
        status, _, finished = _status(conn, job_id)
    assert status == "done"
    assert finished is not None
    assert marks == []  # default does NOT mark crawled


def test_status_table_pipeline_jobs_writes_pipeline():
    """--status-table pipeline_jobs routes set_status to public.pipeline_jobs."""
    engine = get_engine()
    with begin(engine) as conn:
        job_id = _new_pipeline_job(conn)
    try:
        enrich_one.run_job(engine, advertiser_id=999042, job_id=job_id,
                           enrich_call=lambda *a, **k: None,
                           score_call=lambda *a, **k: None,
                           status_table="pipeline_jobs")
        with begin(engine) as conn:
            status, _, finished = _pipeline_status(conn, job_id)
        assert status == "done"
        assert finished is not None
    finally:
        with begin(engine) as conn:
            conn.execute(text("DELETE FROM public.pipeline_jobs WHERE id=:i"),
                         {"i": job_id})


def test_bogus_status_table_rejected():
    """--status-table bogus is rejected by the allowlist (argparse error)."""
    with pytest.raises(SystemExit):
        enrich_one.main(["--advertiser-id", "1", "--job-id", "1",
                         "--status-table", "bogus"])


def test_mark_crawled_called_on_success(monkeypatch):
    engine = get_engine()
    marks = []
    monkeypatch.setattr(enrich_one, "mark_crawled",
                        lambda conn, etype, eid: marks.append((etype, eid)))
    with begin(engine) as conn:
        job_id = _new_job(conn)

    enrich_one.run_job(engine, advertiser_id=999001, job_id=job_id,
                       enrich_call=lambda *a, **k: None,
                       score_call=lambda *a, **k: None,
                       mark_crawled_on_success=True)

    assert marks == [("advertiser", 999001)]


def test_mark_crawled_not_called_on_error(monkeypatch):
    engine = get_engine()
    marks = []
    monkeypatch.setattr(enrich_one, "mark_crawled",
                        lambda conn, etype, eid: marks.append((etype, eid)))
    with begin(engine) as conn:
        job_id = _new_job(conn)

    def boom(engine_, advertiser_id, **kw):
        raise RuntimeError("nope")

    try:
        enrich_one.run_job(engine, advertiser_id=999001, job_id=job_id,
                           enrich_call=boom, score_call=lambda *a, **k: None,
                           mark_crawled_on_success=True)
    except RuntimeError:
        pass

    assert marks == []  # error path never marks crawled
    with begin(engine) as conn:
        status, _, _ = _status(conn, job_id)
    assert status == "error"
