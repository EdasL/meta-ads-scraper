"""Stage-2 triage: pure decision rules, lookup-failure handling, and promotion of
keepers into advertisers/apps. TRIAGETEST* sentinels; pg_conn rolls back."""
from agf.discovery.triage import triage_decision, run_triage
from agf.db import candidates as cand
from agf.enrich import AppMeta
from sqlalchemy import text


def meta(**kw):
    base = dict(track_id="111", name="App", seller="Dev", genre="Health & Fitness",
                release_date="2026-05-01", rating_count=100, avg_rating=4.5,
                installs=None, is_game=False)
    return AppMeta(**{**base, **kw})


# --- decision rules ----------------------------------------------------------
def test_decision_rules():
    assert triage_decision(None) == ("dropped", "no_us_listing")
    assert triage_decision(meta(is_game=True)) == ("dropped", "game")
    # Installs no longer gate (user 2026-06-27): Play's cumulative maxInstalls dropped
    # ~83% of Android candidates on a signal iOS never exposes. Ratings is the gate now.
    assert triage_decision(meta(installs=5_000_000, rating_count=100)) == ("kept", None)
    assert triage_decision(meta(rating_count=5_001)) == ("dropped", "ratings_over_threshold")
    assert triage_decision(meta(installs=5_000_000, rating_count=5_000)) == ("kept", None)
    assert triage_decision(meta(is_game=None)) == ("kept", None)   # unknown passes (repo rule)


# --- orchestration -----------------------------------------------------------
def _seed(pg_conn, store="ios", store_id="881100", page_id="TRIAGETEST1"):
    # run_triage processes EVERY status='new' row, so park any committed leftovers
    # from other suites first (rolled back with the txn — shared-DB isolation lesson).
    pg_conn.execute(text(
        "UPDATE discovery.candidates SET status='dropped', drop_reason='test_isolation' "
        "WHERE status='new' AND page_id NOT LIKE 'TRIAGETEST%'"))
    cid, _ = cand.upsert_sweep_candidate(pg_conn, dict(
        page_id=page_id, page_name="Triage Test", page_like_count=800,
        store=store, store_id=store_id, link_url="https://x", cta_type=None,
        ad_start_date=None, source_keyword="k", technique="t"))
    return cid


def test_keeper_is_promoted(pg_conn):
    cid = _seed(pg_conn)
    stats = run_triage(pg_conn,
                       lookup_ios=lambda ids: {"881100": meta(track_id="881100")},
                       lookup_android=lambda ids: {})
    assert stats["kept"] == 1 and stats["dropped"] == {}
    row = pg_conn.execute(text(
        "SELECT status, kept_advertiser_id FROM discovery.candidates WHERE id=:i"),
        {"i": cid}).one()
    assert row.status == "kept" and row.kept_advertiser_id is not None
    adv = pg_conn.execute(text(
        "SELECT page_name, page_like_count FROM public.advertisers WHERE id=:i"),
        {"i": row.kept_advertiser_id}).one()
    assert adv.page_name == "Triage Test" and adv.page_like_count == 800
    app = pg_conn.execute(text(
        "SELECT name, is_game, store FROM public.apps WHERE store='ios' AND store_id='881100'")).one()
    assert app.name == "App" and app.is_game is False


def test_android_keeper_gets_android_store(pg_conn):
    _seed(pg_conn, store="android", store_id="com.triage.test", page_id="TRIAGETEST2")
    run_triage(pg_conn, lookup_ios=lambda ids: {},
               lookup_android=lambda ids: {"com.triage.test": meta(track_id="com.triage.test",
                                                                   installs=4_000)})
    app = pg_conn.execute(text(
        "SELECT store, installs FROM public.apps WHERE store_id='com.triage.test'")).one()
    assert app.store == "android" and app.installs == 4_000


def test_missing_listing_drops(pg_conn):
    cid = _seed(pg_conn, store_id="000000", page_id="TRIAGETEST3")
    stats = run_triage(pg_conn, lookup_ios=lambda ids: {}, lookup_android=lambda ids: {})
    assert stats["dropped"] == {"no_us_listing": 1}
    status = pg_conn.execute(text(
        "SELECT status FROM discovery.candidates WHERE id=:i"), {"i": cid}).scalar()
    assert status == "dropped"


def test_lookup_exception_leaves_candidates_new(pg_conn):
    cid = _seed(pg_conn, page_id="TRIAGETEST4")

    def boom(ids):
        raise OSError("itunes down")
    stats = run_triage(pg_conn, lookup_ios=boom, lookup_android=lambda ids: {})
    assert stats["lookup_failed_ios"] is True and stats["kept"] == 0
    status = pg_conn.execute(text(
        "SELECT status FROM discovery.candidates WHERE id=:i"), {"i": cid}).scalar()
    assert status == "new"                      # retried next run


def test_gplay_sidecar_crash_leaves_android_new(pg_conn):
    cid = _seed(pg_conn, store="android", store_id="com.x.crash", page_id="TRIAGETEST5")
    # lookup_google_play returns {} when the sidecar crashes — with android ids pending,
    # an empty dict is indistinguishable from "all delisted", so treat it as a failure.
    stats = run_triage(pg_conn, lookup_ios=lambda ids: {}, lookup_android=lambda ids: {})
    assert stats["lookup_failed_android"] is True
    status = pg_conn.execute(text(
        "SELECT status FROM discovery.candidates WHERE id=:i"), {"i": cid}).scalar()
    assert status == "new"
