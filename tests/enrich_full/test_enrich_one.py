from sqlalchemy import text
from agf.db.engine import begin, get_engine
from agf.enrich_full import enrich_one


def _new_job(conn, advertiser_id=999001):
    row = conn.execute(text(
        "INSERT INTO public.enrich_jobs (advertiser_id) VALUES (:a) RETURNING id"
    ), {"a": advertiser_id}).first()
    return row[0]


def _status(conn, job_id):
    return conn.execute(text(
        "SELECT status, error_text, finished_at FROM public.enrich_jobs WHERE id=:i"
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
