"""One-shot funnel-confirm CLI unit (subprocess unit for funnel_confirm_daemon).
Uses the committing _migrated_engine with injected fake resolve/probe."""
from types import SimpleNamespace
from sqlalchemy import text

from agf.db.engine import begin
from agf.db.pipeline_jobs import enqueue_jobs
from agf.discovery.funnel_confirm_one import run


def _seed(engine):
    from agf.db.funnel_candidates import upsert_funnel_candidate
    with begin(engine) as conn:
        conn.execute(text("DELETE FROM discovery.funnel_candidates WHERE page_id='FC1ONE'"))
        conn.execute(text("DELETE FROM logging.crawl_log WHERE entity_type='funnel_confirm' AND entity_id >= 990000000"))
        upsert_funnel_candidate(conn, dict(
            page_id="FC1ONE", page_name="One", page_like_count=1, cta_type="LEARN_MORE",
            utm_source=None, utm_medium=None, utm_campaign=None, utm_content=None,
            ad_start_date=None, source_keyword="q", technique="funnel_phrase",
            funnel_host="quiz.", landing_url="https://fc1one.io/quiz/start",
            landing_domain="fc1one.io"))
        cid = conn.execute(text("SELECT id FROM discovery.funnel_candidates WHERE page_id='FC1ONE'")).scalar_one()
        enqueue_jobs(conn, "funnel_confirm", [cid])
        jid = conn.execute(text("SELECT id FROM public.pipeline_jobs WHERE kind='funnel_confirm' AND advertiser_id=:c ORDER BY id DESC LIMIT 1"), {"c": cid}).scalar_one()
    return cid, jid


def test_run_marks_job_done_and_crawl_log(_migrated_engine):
    eng = _migrated_engine
    cid, jid = _seed(eng)
    try:
        rc = run(eng, cid, jid,
                 resolve=lambda _: SimpleNamespace(kind="web", store_id=None),
                 probe=lambda _: SimpleNamespace(landing_html="<html>x</html>", quiz_steps=2))
        assert rc == 0
        with eng.connect() as c:
            assert c.execute(text("SELECT status FROM public.pipeline_jobs WHERE id=:j"), {"j": jid}).scalar() == "done"
            assert c.execute(text("SELECT status FROM discovery.funnel_candidates WHERE id=:i"), {"i": cid}).scalar() == "confirmed"
            mk = c.execute(text("SELECT crawl_result FROM logging.crawl_log WHERE entity_type='funnel_confirm' AND entity_id=:i ORDER BY crawled_at DESC LIMIT 1"), {"i": cid}).scalar()
        assert mk is not None
    finally:
        with begin(eng) as conn:
            conn.execute(text("DELETE FROM public.pipeline_jobs WHERE kind='funnel_confirm' AND advertiser_id=:c"), {"c": cid})
            conn.execute(text("DELETE FROM discovery.funnel_candidates WHERE id=:i"), {"i": cid})
            conn.execute(text("DELETE FROM logging.crawl_log WHERE entity_type='funnel_confirm' AND entity_id=:i"), {"i": cid})
