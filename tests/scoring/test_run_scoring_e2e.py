"""Live e2e against real Postgres. Uses _migrated_engine directly (not pg_conn)
because REFRESH ... CONCURRENTLY needs committed rows + an autocommit connection,
which the rolled-back pg_conn fixture cannot provide. Cleans up its sentinels."""
from datetime import date, datetime, timezone, timedelta
from sqlalchemy import text
from agf.parse import AdRecord
from agf.db import repo
from agf.db.engine import begin
from agf.scoring.run_scoring import score_all, refresh_emerging_matview

AS_OF = date(2026, 6, 4)
NOW = int(datetime(2026, 6, 4, tzinfo=timezone.utc).timestamp())
DAY = 86_400
WEEK0 = date(2026, 3, 2)
SENTINELS = ("P5GEM", "P5BRAND", "P5SPIKE")

# pid: (genre, rating_count, release_date, is_emerging, longevity_days,
#       weekly active-ad-count series, weekly total_eu_reach, has_funnel)
SPECS = {
    "P5GEM":   ("Health & Fitness", 80,        "2026-05-25", True, 25,
                [2, 5, 12, 30], 50, True),
    "P5BRAND": ("Health & Fitness", 8_000_000, "2017-01-01", False, 500,
                [80, 81, 82, 83], 9_000_000, False),
    "P5SPIKE": ("Education", 300, "2026-05-01", True, 30,
                [2, 1, 2, 6] * 3, 60, False),   # period-4 seasonal, ends on a peak
}


def _seed(conn):
    ids = {}
    for pid, (genre, ratings, rel, emerging, longd, series, reach, funnel) in SPECS.items():
        ar = AdRecord(f"{pid}-AD", pid, pid, True, NOW - longd * DAY, None, 5,
                      "INSTALL_MOBILE_APP", "Install",
                      f"https://apps.apple.com/us/app/x/id{pid}", "body")
        adv = repo.upsert_advertiser(conn, ar)
        app = repo.upsert_app(conn, {"store": "ios", "store_id": pid, "name": pid,
                                     "genre": genre, "rating_count": ratings,
                                     "release_date": rel, "is_emerging": emerging})
        ad_id = repo.upsert_ad(conn, ar, adv, app)
        if funnel:
            repo.upsert_funnel(conn, ad_id, f"https://{pid}.example",
                               f"https://{pid}.example/checkout", "shot.png")
        for i, n in enumerate(series):
            repo.upsert_app_advertiser_metric(conn, adv, WEEK0 + timedelta(weeks=i),
                                              n, n, reach)
        ids[pid] = adv
    # the gem must pass the quality vetoes too: not a game, small installs/likes
    # (raw UPDATE: is_game persistence + meta signals are other write paths)
    conn.execute(text("UPDATE public.apps SET is_game=FALSE, installs=40000 "
                      "WHERE store_id='P5GEM'"))
    conn.execute(text("UPDATE public.advertisers SET page_like_count=2500 "
                      "WHERE id=:i"), {"i": ids["P5GEM"]})
    return ids


def _cleanup(engine):
    with begin(engine) as conn:
        ids = conn.execute(text("SELECT id FROM public.advertisers WHERE page_id = ANY(:p)"),
                           {"p": list(SENTINELS)}).scalars().all()
        if ids:
            conn.execute(text("DELETE FROM adtech.advertiser_scores WHERE advertiser_id = ANY(:i)"), {"i": ids})
            conn.execute(text("DELETE FROM adtech.app_advertiser_metrics WHERE advertiser_id = ANY(:i)"), {"i": ids})
            conn.execute(text("DELETE FROM adtech.ad_metrics m USING public.ads a "
                              "WHERE m.ad_id=a.id AND a.advertiser_id = ANY(:i)"), {"i": ids})
            conn.execute(text("DELETE FROM public.funnels f USING public.ads a "
                              "WHERE f.ad_id=a.id AND a.advertiser_id = ANY(:i)"), {"i": ids})
            conn.execute(text("DELETE FROM public.creatives c USING public.ads a "
                              "WHERE c.ad_id=a.id AND a.advertiser_id = ANY(:i)"), {"i": ids})
            conn.execute(text("DELETE FROM public.ads WHERE advertiser_id = ANY(:i)"), {"i": ids})
            conn.execute(text("DELETE FROM public.advertisers WHERE id = ANY(:i)"), {"i": ids})
        conn.execute(text("DELETE FROM public.apps WHERE store_id = ANY(:p)"), {"p": list(SENTINELS)})
    refresh_emerging_matview(engine)


def test_score_all_flags_gem_not_brand_not_spike(_migrated_engine):
    eng = _migrated_engine
    _cleanup(eng)
    try:
        with begin(eng) as conn:
            ids = _seed(conn)
            # only_ids isolates scoring to these sentinels so the assertions hold even if
            # the shared DB holds other advertisers in the same cohort (e.g. a demo seed)
            score_all(conn, AS_OF, only_ids=list(ids.values()))
        refresh_emerging_matview(eng)
        with eng.connect() as conn:
            rows = {r.advertiser_id: r for r in conn.execute(text(
                "SELECT advertiser_id, gem_score, alert, cohort, app_name "
                "FROM frontend.emerging_scaling_apps WHERE advertiser_id = ANY(:i)"),
                {"i": list(ids.values())}).all()}
            scores = {r.advertiser_id: r for r in conn.execute(text(
                "SELECT advertiser_id, vetoed, veto_reason, gem_score "
                "FROM adtech.advertiser_scores WHERE advertiser_id = ANY(:i)"),
                {"i": list(ids.values())}).all()}
        gem, brand, spike = ids["P5GEM"], ids["P5BRAND"], ids["P5SPIKE"]
        assert rows[gem].alert is True                      # the gem alerts (>=70, not vetoed)
        assert scores[gem].vetoed is False
        assert rows[gem].gem_score > scores[brand].gem_score
        # GemScore v2: the seasonal-spike veto is gone — a seasonal-looking series
        # is no longer disqualifying, so the spike sentinel reaches the feed.
        assert scores[spike].vetoed is False
        assert spike in rows
        assert scores[brand].vetoed is True                 # 8M ratings == established, not a gem
        assert scores[brand].veto_reason == "established_traction"
        assert brand not in rows
        assert rows[gem].cohort == "Health & Fitness×US"
    finally:
        _cleanup(eng)
