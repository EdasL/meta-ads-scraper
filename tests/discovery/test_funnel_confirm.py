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
    k, s, store, sid, reason, payoff = classify_funnel("<html>x</html>", _resolved("ios", "999"))
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
    k, s, _, _, reason, _ = classify_funnel("fbq('init')", _resolved("web"))
    assert (k, s, reason) == ("unknown", "dropped", "no_quiz_no_payoff")


def test_classify_pixel_plus_funnel_host_confirms():
    k, s, *_ = classify_funnel("fbq('init')", _resolved("web"), funnel_host="quiz.")
    assert (k, s) == ("unknown", "confirmed")


def test_classify_pixel_on_bare_homepage_drops():
    # A pixel on a funnel host but a ROOT path is a homepage, not a funnel —
    # this is what surfaced calorii.com/ as a fake "Quiz" link. Must drop.
    k, s, _, _, reason, _ = classify_funnel(
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
    k, s, _, _, reason, _ = classify_funnel("<html>nothing</html>", _resolved("web"),
                                             funnel_host="quiz.")
    assert (k, s, reason) == ("unknown", "dropped", "no_quiz_no_payoff")


def test_classify_one_quiz_screen_is_not_enough():
    k, s, _, _, reason, _ = classify_funnel("<html>nothing</html>", _resolved("web"),
                                             quiz_steps=1)
    assert (k, s, reason) == ("unknown", "dropped", "no_quiz_no_payoff")


def test_classify_drops_generic_web():
    k, s, store, sid, reason, payoff = classify_funnel("<html>just a giftcard shop</html>", _resolved("web"))
    assert (k, s, reason) == ("unknown", "dropped", "no_quiz_no_payoff")


# --- unit 4: 6-tuple + DOM store_id extraction + payoff_kind ---

def test_classify_returns_6tuple():
    result = classify_funnel("<html>x</html>", _resolved("ios", "999"))
    assert len(result) == 6


def test_classify_payoff_kind_app_from_resolved():
    k, s, store, sid, reason, payoff = classify_funnel("<html>x</html>", _resolved("ios", "999"))
    assert payoff == "app"
    assert (k, s, store, sid) == ("install", "confirmed", "ios", "999")


def test_classify_store_id_from_dom_when_resolved_has_none():
    html = '<a href="https://apps.apple.com/us/app/betterme/id1234567890">Download</a>'
    k, s, store, sid, reason, payoff = classify_funnel(html, _resolved("web"))
    assert (k, s) == ("install", "confirmed")
    assert store == "ios"
    assert sid == "1234567890"
    assert payoff == "app"


def test_classify_payoff_kind_physical():
    html = "cdn.shopify.com js.stripe.com/v3 fbq('init')"
    k, s, store, sid, reason, payoff = classify_funnel(html, _resolved("web"))
    assert payoff == "physical"


def test_classify_payoff_kind_web_sub():
    html = 'js.stripe.com/v3 fbq("init") cancel anytime'
    k, s, store, sid, reason, payoff = classify_funnel(html, _resolved("web"))
    assert payoff == "web_sub"


def test_classify_payoff_kind_leadgen():
    k, s, store, sid, reason, payoff = classify_funnel("<html>generic</html>", _resolved("web"))
    assert payoff == "leadgen"


# --- unit: physical + app badge — no bridge (FIX 3) ---

def test_classify_physical_with_app_badge_not_bridged():
    # A Shopify/cart page that also embeds an App Store badge must NOT get a store_id
    # via DOM extraction — physical signal wins and store_ref_from_html is suppressed.
    html = ('cdn.shopify.com js.stripe.com/v3 '
            '<a href="https://apps.apple.com/us/app/myshop/id5555555555">Download App</a>')
    k, s, store, sid, reason, payoff = classify_funnel(html, _resolved("web"))
    assert payoff == "physical", "Shopify page must be classified physical"
    assert sid is None, "store_id must be None — DOM extraction suppressed for physical pages"
    assert store is None


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


# --- unit 6: confirm-time app bridge ---

def test_run_funnel_confirm_probe_crash_leaves_row_new(_migrated_engine):
    """A probe that RAISES (browser missing/dead — the 06-26..07-06 prod incident) must
    NOT write a verdict: classifying html='' funnels everything into payoff 'leadgen',
    a discarded bucket. The row stays 'new' and retries when the probe is healthy."""
    eng = _migrated_engine
    from agf.db.funnel_candidates import upsert_funnel_candidate
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM discovery.funnel_candidates WHERE page_id LIKE 'FCRASH%'"))
        upsert_funnel_candidate(conn, dict(
            page_id="FCRASH1", page_name="C", page_like_count=1,
            cta_type="LEARN_MORE", utm_source=None, utm_medium=None,
            utm_campaign=None, utm_content=None, ad_start_date=None,
            source_keyword="take the quiz", technique="funnel_phrase",
            funnel_host=None, landing_url="https://fcrash.io/s",
            landing_domain="fcrash.io"))

    def boom_probe(url):
        raise ModuleNotFoundError("No module named 'playwright'")

    try:
        stats = run_funnel_confirm(eng, resolve=lambda url: _resolved("web"),
                                   probe=boom_probe, ids=_ids(eng, "FCRASH"))
        assert stats.get("probe_failed", 0) == 1
        assert stats["confirmed"] == 0 and stats["dropped"] == 0
        with eng.connect() as c:
            row = c.execute(text(
                "SELECT status, payoff_kind FROM discovery.funnel_candidates "
                "WHERE page_id = 'FCRASH1'")).one()
        assert row.status == "new"           # no verdict from a dead probe
        assert row.payoff_kind is None
    finally:
        with eng.begin() as conn:
            conn.execute(text("DELETE FROM discovery.funnel_candidates WHERE page_id LIKE 'FCRASH%'"))


def test_run_funnel_confirm_bridges_app(_migrated_engine):
    """When classify_funnel returns payoff_kind='app' + a store_id, run_funnel_confirm
    upserts a discovery.candidates row and tracks bridged_app_new/bridged_app_seen."""
    eng = _migrated_engine
    from agf.db.funnel_candidates import upsert_funnel_candidate
    APP_HTML = '<a href="https://apps.apple.com/us/app/myapp/id9876543">Get it</a>'
    BASE = dict(page_name="BridgeApp", page_like_count=50,
                cta_type="LEARN_MORE", utm_source=None, utm_medium=None,
                utm_campaign=None, utm_content=None, ad_start_date=None,
                source_keyword="weight loss quiz", technique="funnel_phrase")
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM discovery.funnel_candidates WHERE page_id = 'FBRIDGE_APP'"))
        conn.execute(text("DELETE FROM discovery.candidates WHERE page_id = 'FBRIDGE_APP'"))
        upsert_funnel_candidate(conn, {**BASE, "page_id": "FBRIDGE_APP", "funnel_host": None,
                                       "landing_url": "https://fbridge-app.io/quiz",
                                       "landing_domain": "fbridge-app.io"})
    def fake_resolve(url):
        return _resolved("web")   # resolver doesn't find it — DOM bridge does
    def fake_probe(url):
        return _probed(APP_HTML)
    try:
        stats = run_funnel_confirm(eng, resolve=fake_resolve, probe=fake_probe,
                                   ids=_ids(eng, "FBRIDGE_APP"))
        assert stats.get("bridged_app_new", 0) + stats.get("bridged_app_seen", 0) >= 1
        with eng.connect() as c:
            cand = c.execute(text(
                "SELECT store, store_id FROM discovery.candidates WHERE page_id = 'FBRIDGE_APP'"
            )).first()
        assert cand is not None, "bridge row missing from discovery.candidates"
        assert cand.store == "ios"
        assert cand.store_id == "9876543"
    finally:
        with eng.begin() as conn:
            conn.execute(text("DELETE FROM discovery.funnel_candidates WHERE page_id = 'FBRIDGE_APP'"))
            conn.execute(text("DELETE FROM discovery.candidates WHERE page_id = 'FBRIDGE_APP'"))
    # Verify payoff_kind is persisted on the funnel_candidates row itself (not just candidates).
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM discovery.funnel_candidates WHERE page_id = 'FBRIDGE_PKIND'"))
        conn.execute(text("DELETE FROM discovery.candidates WHERE page_id = 'FBRIDGE_PKIND'"))
        upsert_funnel_candidate(conn, {**BASE, "page_id": "FBRIDGE_PKIND", "funnel_host": None,
                                       "landing_url": "https://fbridge-pkind.io/quiz",
                                       "landing_domain": "fbridge-pkind.io"})
    try:
        stats2 = run_funnel_confirm(eng, resolve=lambda _: _resolved("web"),
                                    probe=lambda _: _probed(APP_HTML),
                                    ids=_ids(eng, "FBRIDGE_PKIND"))
        with eng.connect() as c:
            fc_row = c.execute(text(
                "SELECT payoff_kind, store_id FROM discovery.funnel_candidates WHERE page_id = 'FBRIDGE_PKIND'"
            )).first()
        assert fc_row is not None
        assert fc_row.payoff_kind == "app", "payoff_kind must be persisted on funnel_candidates"
        assert fc_row.store_id == "9876543"
    finally:
        with eng.begin() as conn:
            conn.execute(text("DELETE FROM discovery.funnel_candidates WHERE page_id = 'FBRIDGE_PKIND'"))
            conn.execute(text("DELETE FROM discovery.candidates WHERE page_id = 'FBRIDGE_PKIND'"))


def test_run_funnel_confirm_physical_no_bridge(_migrated_engine):
    """A physical/Shopify funnel must not produce a discovery.candidates bridge row.
    Stats bridged_app_new and bridged_app_seen must both be 0."""
    eng = _migrated_engine
    from agf.db.funnel_candidates import upsert_funnel_candidate
    PHYSICAL_HTML = ('cdn.shopify.com js.stripe.com/v3 fbq("init") '
                     '<a href="https://apps.apple.com/us/app/shop/id9999999999">Download</a>')
    BASE = dict(page_name="PhysShop", page_like_count=10,
                cta_type="SHOP_NOW", utm_source=None, utm_medium=None,
                utm_campaign=None, utm_content=None, ad_start_date=None,
                source_keyword="organic dog food", technique="funnel_phrase")
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM discovery.funnel_candidates WHERE page_id = 'FPHYS'"))
        conn.execute(text("DELETE FROM discovery.candidates WHERE page_id = 'FPHYS'"))
        upsert_funnel_candidate(conn, {**BASE, "page_id": "FPHYS", "funnel_host": None,
                                       "landing_url": "https://fphys.io/shop",
                                       "landing_domain": "fphys.io"})
    try:
        stats = run_funnel_confirm(eng, resolve=lambda _: _resolved("web"),
                                   probe=lambda _: _probed(PHYSICAL_HTML),
                                   ids=_ids(eng, "FPHYS"))
        assert stats.get("bridged_app_new", 0) == 0, "physical funnel must not bridge to candidates"
        assert stats.get("bridged_app_seen", 0) == 0
        with eng.connect() as c:
            cand = c.execute(text(
                "SELECT id FROM discovery.candidates WHERE page_id = 'FPHYS'"
            )).first()
        assert cand is None, "no discovery.candidates row must exist for a physical funnel"
        # Verify payoff_kind persisted on funnel_candidates
        with eng.connect() as c:
            fc_row = c.execute(text(
                "SELECT payoff_kind, store_id FROM discovery.funnel_candidates WHERE page_id = 'FPHYS'"
            )).first()
        assert fc_row.payoff_kind == "physical"
        assert fc_row.store_id is None, "store_id must not be set for physical funnel"
    finally:
        with eng.begin() as conn:
            conn.execute(text("DELETE FROM discovery.funnel_candidates WHERE page_id = 'FPHYS'"))
            conn.execute(text("DELETE FROM discovery.candidates WHERE page_id = 'FPHYS'"))
