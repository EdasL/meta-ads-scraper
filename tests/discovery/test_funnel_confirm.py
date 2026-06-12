# tests/discovery/test_funnel_confirm.py
from types import SimpleNamespace
from sqlalchemy import text

from agf.discovery.funnel_confirm import classify_funnel, run_funnel_confirm


def _resolved(kind, store_id=None):
    return SimpleNamespace(kind=kind, store_id=store_id)


# --- pure classifier ---------------------------------------------------------
def test_classify_install_from_resolved_store():
    k, s, store, sid, reason = classify_funnel("<html>x</html>", _resolved("ios", "999"))
    assert (k, s, store, sid) == ("install", "confirmed", "ios", "999")


def test_classify_install_from_mmp_in_html():
    k, s, *_ = classify_funnel('<a href="https://b.onelink.me/x">app</a>', _resolved("web"))
    assert (k, s) == ("install", "confirmed")


def test_classify_subscribe_from_checkout():
    k, s, *_ = classify_funnel('<script src="https://cdn.solidgate.com/js/solid-form.js">', _resolved("web"))
    assert (k, s) == ("subscribe", "confirmed")


def test_classify_unknown_kept_when_pixel_or_host():
    k, s, *_ = classify_funnel("fbq('init')", _resolved("web"))
    assert (k, s) == ("unknown", "confirmed")
    k2, s2, *_ = classify_funnel("<html>nothing</html>", _resolved("web"), funnel_host="quiz.")
    assert (k2, s2) == ("unknown", "confirmed")


def test_classify_drops_generic_web():
    k, s, store, sid, reason = classify_funnel("<html>just a giftcard shop</html>", _resolved("web"))
    assert (k, s, reason) == ("unknown", "dropped", "no_funnel_tech")


# --- orchestrator (fakes; uses the migrated DB only for the candidate rows) ---
def _ids(eng, prefix):
    with eng.connect() as c:
        return [r.id for r in c.execute(text(
            "SELECT id FROM discovery.funnel_candidates WHERE page_id LIKE :p"),
            {"p": prefix + "%"}).all()]


def test_run_funnel_confirm_labels_pool(_migrated_engine):
    eng = _migrated_engine
    from agf.db.funnel_candidates import upsert_funnel_candidate
    BASE = dict(page_name="C", page_like_count=1,
                cta_type="LEARN_MORE", utm_source=None, utm_medium=None,
                utm_campaign=None, utm_content=None, ad_start_date=None,
                source_keyword="take the quiz", technique="funnel_phrase")
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM discovery.funnel_candidates WHERE page_id LIKE 'FCONF%'"))
        upsert_funnel_candidate(conn, {**BASE, "page_id": "FCONF_SUB", "funnel_host": "quiz.",
                                       "landing_url": "https://fconf-sub.io/s",
                                       "landing_domain": "fconf-sub.io"})
        upsert_funnel_candidate(conn, {**BASE, "page_id": "FCONF_DROP", "funnel_host": None,
                                       "landing_url": "https://fconf-drop.io/s",
                                       "landing_domain": "fconf-drop.io"})
    RENDER = {"https://fconf-sub.io/s": '<script src="https://js.stripe.com/v3/"></script>fbq("init")',
              "https://fconf-drop.io/s": "<html>generic</html>"}
    def fake_resolve(url):
        return _resolved("web")
    def fake_render(url):
        return RENDER[url]
    try:
        stats = run_funnel_confirm(eng, resolve=fake_resolve, render=fake_render,
                                   ids=_ids(eng, "FCONF"))
        assert stats["confirmed"] >= 1 and stats["dropped"] >= 1
        with eng.connect() as c:
            rows = {r.page_id: (r.kind, r.status) for r in c.execute(text(
                "SELECT page_id, kind, status FROM discovery.funnel_candidates WHERE page_id LIKE 'FCONF%'")).all()}
        assert rows["FCONF_SUB"] == ("subscribe", "confirmed")
        assert rows["FCONF_DROP"][1] == "dropped"
    finally:
        with eng.begin() as conn:
            conn.execute(text("DELETE FROM discovery.funnel_candidates WHERE page_id LIKE 'FCONF%'"))
