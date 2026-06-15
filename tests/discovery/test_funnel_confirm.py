# tests/discovery/test_funnel_confirm.py
from types import SimpleNamespace
from sqlalchemy import text

from agf.discovery.funnel_confirm import classify_funnel, run_funnel_confirm


def _resolved(kind, store_id=None):
    return SimpleNamespace(kind=kind, store_id=store_id)


def _probed(html, quiz_steps=0):
    return SimpleNamespace(landing_html=html, quiz_steps=quiz_steps)


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


def test_classify_verified_quiz_confirms_without_tech():
    k, s, *_ = classify_funnel("<html>nothing</html>", _resolved("web"), quiz_steps=2)
    assert (k, s) == ("unknown", "confirmed")


def test_classify_pixel_alone_no_longer_confirms():
    # half the web runs GTM — a pixel without a funnel host or quiz evidence drops
    k, s, _, _, reason = classify_funnel("fbq('init')", _resolved("web"))
    assert (k, s, reason) == ("unknown", "dropped", "no_quiz_no_payoff")


def test_classify_pixel_plus_funnel_host_confirms():
    k, s, *_ = classify_funnel("fbq('init')", _resolved("web"), funnel_host="quiz.")
    assert (k, s) == ("unknown", "confirmed")


def test_classify_pixel_on_bare_homepage_drops():
    # A pixel on a funnel host but a ROOT path is a homepage, not a funnel —
    # this is what surfaced calorii.com/ as a fake "Quiz" link. Must drop.
    k, s, _, _, reason = classify_funnel(
        "fbq('init')", _resolved("web"), funnel_host="calorii.",
        funnel_url="https://calorii.com/")
    assert (k, s, reason) == ("unknown", "dropped", "no_quiz_no_payoff")


def test_classify_pixel_on_funnel_path_confirms():
    # Same pixel + host, but a real funnel path (/quiz) — confirms.
    k, s, *_ = classify_funnel(
        "fbq('init')", _resolved("web"), funnel_host="calorii.",
        funnel_url="https://calorii.com/quiz/start")
    assert (k, s) == ("unknown", "confirmed")


def test_classify_funnel_host_alone_no_longer_confirms():
    k, s, _, _, reason = classify_funnel("<html>nothing</html>", _resolved("web"),
                                         funnel_host="quiz.")
    assert (k, s, reason) == ("unknown", "dropped", "no_quiz_no_payoff")


def test_classify_one_quiz_screen_is_not_enough():
    k, s, _, _, reason = classify_funnel("<html>nothing</html>", _resolved("web"),
                                         quiz_steps=1)
    assert (k, s, reason) == ("unknown", "dropped", "no_quiz_no_payoff")


def test_classify_drops_generic_web():
    k, s, store, sid, reason = classify_funnel("<html>just a giftcard shop</html>", _resolved("web"))
    assert (k, s, reason) == ("unknown", "dropped", "no_quiz_no_payoff")


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
        upsert_funnel_candidate(conn, {**BASE, "page_id": "FCONF_QUIZ", "funnel_host": None,
                                       "landing_url": "https://fconf-quiz.io/s",
                                       "landing_domain": "fconf-quiz.io"})
        upsert_funnel_candidate(conn, {**BASE, "page_id": "FCONF_DROP", "funnel_host": None,
                                       "landing_url": "https://fconf-drop.io/s",
                                       "landing_domain": "fconf-drop.io"})
    PROBE = {"https://fconf-sub.io/s":
                 _probed('<script src="https://js.stripe.com/v3/"></script>fbq("init")'),
             "https://fconf-quiz.io/s": _probed("<html>a quiz, no tech</html>", quiz_steps=2),
             "https://fconf-drop.io/s": _probed("<html>generic</html>")}
    def fake_resolve(url):
        return _resolved("web")
    def fake_probe(url):
        return PROBE[url]
    try:
        stats = run_funnel_confirm(eng, resolve=fake_resolve, probe=fake_probe,
                                   ids=_ids(eng, "FCONF"))
        assert stats["confirmed"] >= 2 and stats["dropped"] >= 1
        with eng.connect() as c:
            rows = {r.page_id: r for r in c.execute(text(
                "SELECT page_id, kind, status, quiz_steps FROM discovery.funnel_candidates "
                "WHERE page_id LIKE 'FCONF%'")).all()}
        assert (rows["FCONF_SUB"].kind, rows["FCONF_SUB"].status) == ("subscribe", "confirmed")
        assert (rows["FCONF_QUIZ"].kind, rows["FCONF_QUIZ"].status) == ("unknown", "confirmed")
        assert rows["FCONF_QUIZ"].quiz_steps == 2        # probe evidence persisted
        assert rows["FCONF_DROP"].status == "dropped"
    finally:
        with eng.begin() as conn:
            conn.execute(text("DELETE FROM discovery.funnel_candidates WHERE page_id LIKE 'FCONF%'"))
