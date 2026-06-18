from sqlalchemy import text

from agf.parse import AdRecord
from agf.db import repo
from agf.enrich_full.funnel_repair import funnel_gap_advertisers


def _ad(ext, link):
    # Distinct page per ext so each seeded ad maps to its own advertiser
    # (upsert_advertiser dedups on page_id).
    return AdRecord(ext, f"PG-{ext}", f"GapCo {ext}", True, 1_714_500_000, None, 1,
                    "INSTALL_MOBILE_APP", "Install", link, "body")


def _seed_feed_app(conn, ext, link, *, cohort="health"):
    """Seed an advertiser + one ad + a non-vetoed score row, then refresh the matview
    so the row is in the feed. Returns (advertiser_id, ad_id)."""
    ar = _ad(ext, link)
    adv = repo.upsert_advertiser(conn, ar)
    ad_id = repo.upsert_ad(conn, ar, adv, None)
    conn.execute(text("INSERT INTO adtech.advertiser_scores (advertiser_id, cohort, gem_score) "
                      "VALUES (:a, :c, 50)"), {"a": adv, "c": cohort})
    return adv, ad_id


def _refresh(conn):
    conn.execute(text("REFRESH MATERIALIZED VIEW frontend.emerging_scaling_apps"))


def test_funnel_gap_advertisers_finds_uncovered_feed_app(pg_conn):
    # Feed app that runs an off-store web ad but has no funnel row → it's the gap.
    adv, _ = _seed_feed_app(pg_conn, "GAP", "https://gapapp.example/?pid=w2a_meta")
    _refresh(pg_conn)
    assert adv in funnel_gap_advertisers(pg_conn, 50)


def test_funnel_gap_advertisers_excludes_store_only_app(pg_conn):
    # App whose only ad points to the App Store is not a funnel gap (nothing to probe).
    adv, _ = _seed_feed_app(pg_conn, "STORE", "https://apps.apple.com/us/app/x/id12345")
    _refresh(pg_conn)
    assert adv not in funnel_gap_advertisers(pg_conn, 50)


def test_funnel_gap_advertisers_excludes_social_and_mmp(pg_conn):
    # Social + MMP destinations bounce off-funnel; neither should register as a gap.
    soc, _ = _seed_feed_app(pg_conn, "SOC", "https://www.instagram.com/socapp")
    mmp, _ = _seed_feed_app(pg_conn, "MMP", "https://socapp.onelink.me/abc?af_ad=x")
    _refresh(pg_conn)
    got = funnel_gap_advertisers(pg_conn, 50)
    assert soc not in got and mmp not in got


def test_funnel_gap_advertisers_excludes_app_with_funnel_already_shown(pg_conn):
    # Once a real web funnel is recorded, the matview shows the link → no longer a gap.
    adv, ad_id = _seed_feed_app(pg_conn, "HASF", "https://hasfunnel.example/quiz?pid=w2a")
    repo.upsert_funnel(pg_conn, ad_id, "https://hasfunnel.example/quiz?pid=w2a",
                       "https://hasfunnel.example/quiz", None, kind="unknown", quiz_steps=2)
    _refresh(pg_conn)
    assert adv not in funnel_gap_advertisers(pg_conn, 50)
