"""Read queries over the frozen §3.6 matview + dossier joins, and the single review
write. conn-first; the read conn is SET READ ONLY by the dependency, the write conn
commits in the dependency."""
from sqlalchemy import text

_MV_COLS = ("advertiser_id, cohort, page_name, app_id, app_name, gem_score, "
            "scaling_velocity, under_radar, funnel_intent, active_ad_count, "
            "max_longevity_days, alert, first_count, latest_count, weeks_observed, "
            "pct_change, refreshed_at")


def shortlist(conn, *, cohort=None, min_score=0.0, limit=50, offset=0):
    return conn.execute(text(f"""
        SELECT {_MV_COLS} FROM frontend.emerging_scaling_apps
        WHERE gem_score >= :min_score
          AND (CAST(:cohort AS text) IS NULL OR cohort = :cohort)
        ORDER BY gem_score DESC, advertiser_id
        LIMIT :limit OFFSET :offset"""),
        {"cohort": cohort, "min_score": min_score, "limit": limit, "offset": offset}
    ).mappings().all()


def gem_dossier(conn, advertiser_id):
    gem = conn.execute(text(f"SELECT {_MV_COLS} FROM frontend.emerging_scaling_apps "
                            "WHERE advertiser_id = :a"), {"a": advertiser_id}).mappings().first()
    if gem is None:
        return None
    domain = conn.execute(text(
        "SELECT d.domain FROM public.advertisers a "
        "LEFT JOIN public.domains d ON d.id = a.domain_id WHERE a.id = :a"),
        {"a": advertiser_id}).scalar()
    apps = conn.execute(text(
        "SELECT DISTINCT ap.store, ap.store_id, ap.name, ap.developer, ap.genre, "
        "       ap.release_date, ap.rating, ap.rating_count, ap.is_emerging, ap.description "
        "FROM public.apps ap JOIN public.ads a ON a.app_id = ap.id "
        "WHERE a.advertiser_id = :a"), {"a": advertiser_id}).mappings().all()
    funnels = conn.execute(text(
        "SELECT f.landing_url, f.final_url_after_redirect, f.screenshot_s3_key "
        "FROM public.funnels f JOIN public.ads a ON a.id = f.ad_id "
        "WHERE a.advertiser_id = :a"), {"a": advertiser_id}).mappings().all()
    creatives = conn.execute(text(
        "SELECT c.type, c.s3_key, c.body_text, c.title "
        "FROM public.creatives c JOIN public.ads a ON a.id = c.ad_id "
        "WHERE a.advertiser_id = :a"), {"a": advertiser_id}).mappings().all()
    enr = conn.execute(text(
        "SELECT a.page_category, a.ig_username, a.ig_followers, p.page_name AS parent_company "
        "FROM public.advertisers a "
        "LEFT JOIN public.advertisers p ON p.id = a.parent_company_id "
        "WHERE a.id = :a"), {"a": advertiser_id}).mappings().first() or {}
    return {"gem": dict(gem), "domain": domain,
            "page_category": enr.get("page_category"),
            "ig_username": enr.get("ig_username"), "ig_followers": enr.get("ig_followers"),
            "parent_company": enr.get("parent_company"),
            "apps": [dict(r) for r in apps], "funnels": [dict(r) for r in funnels],
            "creatives": [dict(r) for r in creatives]}


def gem_scaling(conn, advertiser_id):
    return conn.execute(text(
        "SELECT week_start, active_ad_count, distinct_creative_count, total_eu_reach "
        "FROM adtech.app_advertiser_metrics WHERE advertiser_id = :a ORDER BY week_start"),
        {"a": advertiser_id}).mappings().all()


def review_queue(conn, *, min_score=70.0, limit=50, offset=0):
    return conn.execute(text("""
        SELECT advertiser_id, cohort, page_name, app_name, gem_score,
               active_ad_count, max_longevity_days
        FROM frontend.emerging_scaling_apps e
        WHERE e.alert = TRUE AND e.gem_score >= :min_score
          AND NOT EXISTS (SELECT 1 FROM frontend.review_decisions r
                          WHERE r.advertiser_id = e.advertiser_id)
        ORDER BY e.gem_score DESC, e.advertiser_id LIMIT :limit OFFSET :offset"""),
        {"min_score": min_score, "limit": limit, "offset": offset}).mappings().all()


def record_decision(conn, advertiser_id, verdict, reviewer, note=None):
    conn.execute(text("""
        INSERT INTO frontend.review_decisions (advertiser_id, verdict, reviewer, note, decided_at)
        VALUES (:a, :v, :r, :n, clock_timestamp())"""),
        {"a": advertiser_id, "v": verdict, "r": reviewer, "n": note})
