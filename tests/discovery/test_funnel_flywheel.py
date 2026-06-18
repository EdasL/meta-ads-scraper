from sqlalchemy import text

from agf.parse import AdRecord
from agf.db import dynamic_keywords as dk
from agf.discovery.keyword_mining import fetch_funnel_copy
from agf.workers.main import run_funnel_flywheel_stage
from tests.discovery.conftest import FakeExtractor, NOW_EPOCH, DAY


def _funnel_ad(link, page_id, body):
    return AdRecord("a", page_id, "F", True, NOW_EPOCH - 30 * DAY, None, 1,
                    "LEARN_MORE", "x", link, body, page_like_count=500)


def _seed_confirmed(conn, pid, body):
    conn.execute(text(
        "INSERT INTO discovery.funnel_candidates (page_id, landing_domain, status, body_text) "
        "VALUES (:p, :d, 'confirmed', :b)"), {"p": pid, "d": pid + ".com", "b": body})


def test_dynamic_keywords_kind_isolation(_migrated_engine):
    eng = _migrated_engine
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM discovery.dynamic_keywords WHERE keyword LIKE 'kindtest%'"))
    try:
        with eng.begin() as conn:
            dk.add_trial_keywords(conn, ["kindtest alpha"], kind="app")
            dk.add_trial_keywords(conn, ["kindtest alpha", "kindtest beta"], kind="funnel")
        with eng.connect() as conn:
            app_tried = dk.tried_keywords(conn, kind="app")
            fun_tried = dk.tried_keywords(conn, kind="funnel")
            app_live = {r.keyword for r in dk.live_keywords(conn, kind="app")}
            fun_live = {r.keyword for r in dk.live_keywords(conn, kind="funnel")}
        # the same phrase lives in BOTH pools independently; neither sees the other's exclusives
        assert "kindtest alpha" in app_tried and "kindtest beta" not in app_tried
        assert {"kindtest alpha", "kindtest beta"} <= fun_tried
        assert "kindtest beta" in fun_live and "kindtest beta" not in app_live
    finally:
        with eng.begin() as conn:
            conn.execute(text("DELETE FROM discovery.dynamic_keywords WHERE keyword LIKE 'kindtest%'"))


def test_fetch_funnel_copy_only_confirmed_with_body(_migrated_engine):
    eng = _migrated_engine
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM discovery.funnel_candidates WHERE page_id LIKE 'FLY%'"))
    try:
        with eng.begin() as conn:
            _seed_confirmed(conn, "FLY1", "take the blood sugar quiz")
            _seed_confirmed(conn, "FLY2", "start the blood sugar quiz")
            conn.execute(text(            # status='new' -> excluded
                "INSERT INTO discovery.funnel_candidates (page_id, landing_domain, status, body_text) "
                "VALUES ('FLY3','fly3.com','new','ignored not confirmed')"))
        with eng.connect() as conn:
            bodies = {r.body_text for r in fetch_funnel_copy(conn) if r.page_id.startswith("FLY")}
        assert {"take the blood sugar quiz", "start the blood sugar quiz"} <= bodies
        assert "ignored not confirmed" not in bodies
    finally:
        with eng.begin() as conn:
            conn.execute(text("DELETE FROM discovery.funnel_candidates WHERE page_id LIKE 'FLY%'"))


def test_funnel_flywheel_mines_and_sweeps_in_its_own_pool(_migrated_engine):
    eng = _migrated_engine
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM discovery.funnel_candidates WHERE page_id LIKE 'FLY%'"))
        conn.execute(text("DELETE FROM discovery.dynamic_keywords WHERE source='funnel_copy'"))
        conn.execute(text("DELETE FROM discovery.candidates WHERE page_id LIKE 'FLY%'"))
    try:
        # two confirmed funnels share the phrase 'blood sugar quiz' (only this 3-gram
        # appears in BOTH bodies, so mine_phrases yields exactly it)
        with eng.begin() as conn:
            _seed_confirmed(conn, "FLY1", "take the blood sugar quiz now")
            _seed_confirmed(conn, "FLY2", "start your blood sugar quiz today")
        ext = FakeExtractor(keyword_ads={
            "blood sugar quiz": [_funnel_ad("https://quiz.sugar.example/start", "FLYNEW",
                                            "blood sugar quiz")],
        })
        stats = run_funnel_flywheel_stage(eng, ext, max_pages=2, min_advertisers=2)
        assert "blood sugar quiz" in stats["mined"]
        assert stats["keywords_swept"] == len(stats["mined"])     # every minted funnel kw swept
        assert stats["candidates_new"] >= 1                       # the swept funnel landed
        with eng.connect() as conn:
            assert "blood sugar quiz" in dk.tried_keywords(conn, kind="funnel")
            assert "blood sugar quiz" not in dk.tried_keywords(conn, kind="app")  # pool isolation
    finally:
        with eng.begin() as conn:
            conn.execute(text("DELETE FROM discovery.funnel_candidates WHERE page_id LIKE 'FLY%'"))
            conn.execute(text("DELETE FROM discovery.dynamic_keywords WHERE source='funnel_copy'"))
            conn.execute(text("DELETE FROM discovery.candidates WHERE page_id LIKE 'FLY%'"))
