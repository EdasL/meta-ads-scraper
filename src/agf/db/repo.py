"""conn-first data-access (master-plan §3.3). Every function takes a SQLAlchemy
Connection as its first arg and runs inside the caller's transaction (no commits)."""
import json
from datetime import datetime, timezone, date
from sqlalchemy import text
from agf.db.dedup import is_duplicate_creative


# --- coercion helpers --------------------------------------------------------
def _ts(epoch):
    """epoch seconds (Plan-1 AdRecord) -> aware datetime, or None."""
    return datetime.fromtimestamp(epoch, tz=timezone.utc) if epoch else None


def _as_date(v):
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s).date()
    except ValueError:
        try:
            return date.fromisoformat(s[:10])
        except ValueError:
            return None


_DESC_CAP = 1500


def _cap_description(value, cap: int = _DESC_CAP):
    """Trim an app-store description to `cap` chars; empty/blank -> None so a later
    NULL never wipes a good value (paired with COALESCE on the upsert)."""
    if not value:
        return None
    text_value = str(value).strip()
    if not text_value:
        return None
    return text_value[:cap]


def _app_dict(meta) -> dict:
    """Accept either a dict of app columns or a Plan-1 AppMeta (iOS)."""
    if isinstance(meta, dict):
        m = dict(meta)
    elif hasattr(meta, "track_id"):       # Plan 1 enrich.AppMeta
        m = {"store": "ios", "store_id": meta.track_id, "name": meta.name,
             "developer": getattr(meta, "seller", None), "genre": meta.genre,
             "release_date": meta.release_date, "rating": getattr(meta, "avg_rating", None),
             "rating_count": meta.rating_count,
             "installs": getattr(meta, "installs", None),
             "icon_url": getattr(meta, "icon_url", None),
             "screenshots": getattr(meta, "screenshots", None),
             "price_label": getattr(meta, "price_label", None),
             "description": getattr(meta, "description", None),
             "is_game": getattr(meta, "is_game", None)}
    else:
        raise TypeError("upsert_app expects a dict or AppMeta")
    if not m.get("store") or not m.get("store_id"):
        raise ValueError("upsert_app needs store + store_id")
    return m


# --- core upserts ------------------------------------------------------------
def upsert_advertiser(conn, ar) -> int:
    """Minimal advertiser insert (page_id, page_name, page_like_count). Enrichment cols
    are Plan-6. page_like_count (snapshot.page_like_count — the cheap incumbent-size
    signal) only updates when the incoming value is non-NULL."""
    return conn.execute(text("""
        INSERT INTO public.advertisers (page_id, page_name, page_like_count)
        VALUES (:page_id, :page_name, :page_like_count)
        ON CONFLICT (page_id) DO UPDATE
          SET page_name = COALESCE(EXCLUDED.page_name, public.advertisers.page_name),
              page_like_count = COALESCE(EXCLUDED.page_like_count,
                                         public.advertisers.page_like_count),
              last_seen = now()
        RETURNING id
    """), {"page_id": ar.page_id, "page_name": getattr(ar, "page_name", None),
           "page_like_count": getattr(ar, "page_like_count", None)}).scalar_one()


def upsert_app(conn, meta) -> int:
    m = _app_dict(meta)
    screenshots = m.get("screenshots")
    return conn.execute(text("""
        INSERT INTO public.apps
          (store, store_id, name, developer, genre, release_date, rating,
           rating_count, installs, free, icon_url, screenshots, price_label,
           is_emerging, description, is_game)
        VALUES
          (:store, :store_id, :name, :developer, :genre, :release_date, :rating,
           :rating_count, :installs, :free, :icon_url, CAST(:screenshots AS jsonb),
           :price_label, :is_emerging, :description, :is_game)
        ON CONFLICT (store, store_id) DO UPDATE SET
          name         = COALESCE(EXCLUDED.name, public.apps.name),
          developer    = COALESCE(EXCLUDED.developer, public.apps.developer),
          genre        = COALESCE(EXCLUDED.genre, public.apps.genre),
          release_date = COALESCE(EXCLUDED.release_date, public.apps.release_date),
          rating       = COALESCE(EXCLUDED.rating, public.apps.rating),
          rating_count = COALESCE(EXCLUDED.rating_count, public.apps.rating_count),
          installs     = COALESCE(EXCLUDED.installs, public.apps.installs),
          free         = COALESCE(EXCLUDED.free, public.apps.free),
          icon_url     = COALESCE(EXCLUDED.icon_url, public.apps.icon_url),
          screenshots  = COALESCE(EXCLUDED.screenshots, public.apps.screenshots),
          price_label  = COALESCE(EXCLUDED.price_label, public.apps.price_label),
          is_emerging  = COALESCE(EXCLUDED.is_emerging, public.apps.is_emerging),
          description  = COALESCE(EXCLUDED.description, public.apps.description),
          is_game      = COALESCE(EXCLUDED.is_game, public.apps.is_game),
          updated_at   = now()
        RETURNING id
    """), {
        "store": m["store"], "store_id": str(m["store_id"]), "name": m.get("name"),
        "developer": m.get("developer"), "genre": m.get("genre"),
        "release_date": _as_date(m.get("release_date")), "rating": m.get("rating"),
        "rating_count": m.get("rating_count"), "installs": m.get("installs"),
        "free": m.get("free"), "icon_url": m.get("icon_url"),
        "screenshots": json.dumps(list(screenshots)) if screenshots else None,
        "price_label": m.get("price_label"),
        "is_emerging": m.get("is_emerging"),
        "description": _cap_description(m.get("description")),
        "is_game": m.get("is_game"),
    }).scalar_one()


def upsert_ad(conn, ar, advertiser_id: int, app_id: int | None,
              meta_rank: int | None = None) -> int:
    """Upsert one ad. `meta_rank` is the ad's 1-based position in Meta's
    impressions-ordered listing (set only by the impressions-ordered re-scrape in
    refresh_one_advertiser); a NULL leaves any existing rank untouched on conflict."""
    return conn.execute(text("""
        INSERT INTO public.ads
          (external_ad_id, advertiser_id, app_id, link_url, cta_type, cta_text,
           publisher_platforms, is_active, start_date, end_date, collation_count, meta_rank)
        VALUES
          (:ext, :adv, :app, :link, :cta_type, :cta_text,
           :platforms, :is_active, :start_date, :end_date, :collation, :meta_rank)
        ON CONFLICT (external_ad_id) DO UPDATE SET
          advertiser_id   = EXCLUDED.advertiser_id,
          app_id          = COALESCE(EXCLUDED.app_id, public.ads.app_id),
          link_url        = COALESCE(EXCLUDED.link_url, public.ads.link_url),
          cta_type        = COALESCE(EXCLUDED.cta_type, public.ads.cta_type),
          cta_text        = COALESCE(EXCLUDED.cta_text, public.ads.cta_text),
          is_active       = EXCLUDED.is_active,
          end_date        = COALESCE(EXCLUDED.end_date, public.ads.end_date),
          collation_count = COALESCE(EXCLUDED.collation_count, public.ads.collation_count),
          meta_rank       = COALESCE(EXCLUDED.meta_rank, public.ads.meta_rank),
          last_seen       = now()
        RETURNING id
    """), {
        "ext": str(ar.ad_archive_id), "adv": advertiser_id, "app": app_id,
        "link": ar.link_url, "cta_type": ar.cta_type, "cta_text": ar.cta_text,
        "platforms": getattr(ar, "publisher_platforms", None),
        "is_active": bool(ar.is_active), "start_date": _ts(ar.start_date),
        "end_date": _ts(ar.end_date), "collation": ar.collation_count,
        "meta_rank": meta_rank,
    }).scalar_one()


def upsert_creative(conn, ad_id, type, body_text, title, snapshot_image_url,
                    s3_key, sha256, phash) -> int:
    """Insert a creative; byte-identical (same sha256) returns the existing row id."""
    existing = is_duplicate_creative(conn, sha256)
    if existing is not None:
        return existing
    return conn.execute(text("""
        INSERT INTO public.creatives
          (ad_id, type, body_text, title, snapshot_image_url, s3_key, sha256, phash)
        VALUES (:ad_id, :type, :body, :title, :snap, :s3, :sha, :phash)
        RETURNING id
    """), {"ad_id": ad_id, "type": type, "body": body_text, "title": title,
           "snap": snapshot_image_url, "s3": s3_key, "sha": sha256, "phash": phash}).scalar_one()


def ensure_text_creative(conn, ad_id, ar) -> bool:
    """Backstop a text-only creatives row (s3_key NULL) for an ad with no creatives yet,
    so every persisted ad renders in the web evidence reel (which lists public.creatives).
    `type` reflects the source ad's media so the reel can label the tile; the enrich
    worker later adds real s3_key media rows for top ads — this never blocks that, and
    no-ops when the ad already has ANY creative (dedup-safe on re-run). Returns True
    when a row was inserted."""
    if conn.execute(text("SELECT 1 FROM public.creatives WHERE ad_id=:a LIMIT 1"),
                    {"a": ad_id}).first():
        return False
    if getattr(ar, "video_urls", None):
        ctype = "video"
    elif getattr(ar, "image_urls", None):
        ctype = "image"
    else:
        ctype = "text"
    conn.execute(text("""
        INSERT INTO public.creatives (ad_id, type, body_text, title)
        VALUES (:ad_id, :type, :body, :title)
    """), {"ad_id": ad_id, "type": ctype, "body": getattr(ar, "body_text", None),
           "title": getattr(ar, "cta_text", None) or None})
    return True


# --- metrics / enrichment ----------------------------------------------------
def upsert_app_metric(conn, app_id, snapshot_date, rating, rating_count, installs) -> None:
    """One adtech.app_metrics row per app per day (the app-side growth series:
    rating_count delta is the iOS download proxy; installs = Play maxInstalls).
    Idempotent on (app_id, snapshot_date)."""
    conn.execute(text("""
        INSERT INTO adtech.app_metrics (app_id, snapshot_date, rating, rating_count, installs)
        VALUES (:app_id, :d, :rating, :rating_count, :installs)
        ON CONFLICT (app_id, snapshot_date) DO UPDATE SET
          rating = EXCLUDED.rating,
          rating_count = EXCLUDED.rating_count,
          installs = EXCLUDED.installs
    """), {"app_id": app_id, "d": _as_date(snapshot_date), "rating": rating,
           "rating_count": rating_count, "installs": installs})


def prune_app_history(conn, keep_days: int = 730) -> tuple[int, int]:
    """Time-bound the app-side history tables. The growth series is the core
    product signal, so the default window is conservative (2 years). Returns
    (app_metrics_deleted, app_reviews_deleted)."""
    m = conn.execute(text(
        "DELETE FROM adtech.app_metrics WHERE snapshot_date < current_date - :d"
    ), {"d": keep_days}).rowcount
    r = conn.execute(text(
        "DELETE FROM public.app_reviews WHERE fetched_at < now() - :d * interval '1 day'"
    ), {"d": keep_days}).rowcount
    return m, r


def upsert_app_reviews(conn, app_id, reviews) -> int:
    """Upsert recent store reviews (public.app_reviews), keyed (app_id, review_id).
    Returns the number of rows written (insert or update)."""
    n = 0
    for r in reviews:
        if not getattr(r, "review_id", None):
            continue
        conn.execute(text("""
            INSERT INTO public.app_reviews
              (app_id, review_id, author, rating, title, content, reviewed_at)
            VALUES (:app_id, :rid, :author, :rating, :title, :content, :reviewed_at)
            ON CONFLICT (app_id, review_id) DO UPDATE SET
              author      = COALESCE(EXCLUDED.author, public.app_reviews.author),
              rating      = COALESCE(EXCLUDED.rating, public.app_reviews.rating),
              title       = COALESCE(EXCLUDED.title, public.app_reviews.title),
              content     = COALESCE(EXCLUDED.content, public.app_reviews.content),
              reviewed_at = COALESCE(EXCLUDED.reviewed_at, public.app_reviews.reviewed_at),
              fetched_at  = now()
        """), {"app_id": app_id, "rid": str(r.review_id), "author": r.author,
               "rating": r.rating, "title": r.title, "content": r.content,
               "reviewed_at": r.reviewed_at})
        n += 1
    return n


def set_meta_active_ads(conn, advertiser_id, count, capped) -> None:
    """Persist the true active-ad count from bounded view_all_page_id pagination.
    capped=True means pagination hit the cap, so `count` is a lower bound ("count+")."""
    conn.execute(text("""
        UPDATE public.advertisers
        SET meta_active_ads = :n,
            meta_active_ads_capped = :capped,
            meta_active_ads_checked_at = now()
        WHERE id = :id
    """), {"id": advertiser_id, "n": count, "capped": bool(capped)})


def record_ad_metric(conn, ad_id, snapshot_date, eu_total_reach, active) -> None:
    conn.execute(text("""
        INSERT INTO adtech.ad_metrics (ad_id, snapshot_date, eu_total_reach, active)
        VALUES (:ad_id, :d, :reach, :active)
        ON CONFLICT (ad_id, snapshot_date) DO UPDATE
          SET eu_total_reach = EXCLUDED.eu_total_reach, active = EXCLUDED.active
    """), {"ad_id": ad_id, "d": _as_date(snapshot_date), "reach": eu_total_reach, "active": active})


def upsert_app_advertiser_metric(conn, advertiser_id, week_start, active_ad_count,
                                 distinct_creative_count, total_eu_reach) -> None:
    conn.execute(text("""
        INSERT INTO adtech.app_advertiser_metrics
          (advertiser_id, week_start, active_ad_count, distinct_creative_count, total_eu_reach)
        VALUES (:adv, :wk, :acount, :ccount, :reach)
        ON CONFLICT (advertiser_id, week_start) DO UPDATE SET
          active_ad_count = EXCLUDED.active_ad_count,
          distinct_creative_count = EXCLUDED.distinct_creative_count,
          total_eu_reach = EXCLUDED.total_eu_reach
    """), {"adv": advertiser_id, "wk": _as_date(week_start), "acount": active_ad_count,
           "ccount": distinct_creative_count, "reach": total_eu_reach})


def upsert_advertiser_growth(conn, advertiser_id, first_count, latest_count,
                             weeks_observed, pct_change) -> None:
    """Upsert the per-advertiser growth summary (adtech.advertiser_growth). One row per
    advertiser; recomputed each scoring run. conn-first: no commit."""
    conn.execute(text("""
        INSERT INTO adtech.advertiser_growth
          (advertiser_id, first_count, latest_count, weeks_observed, pct_change, computed_at)
        VALUES (:adv, :first, :latest, :weeks, :pct, clock_timestamp())
        ON CONFLICT (advertiser_id) DO UPDATE SET
          first_count    = EXCLUDED.first_count,
          latest_count   = EXCLUDED.latest_count,
          weeks_observed = EXCLUDED.weeks_observed,
          pct_change     = EXCLUDED.pct_change,
          computed_at    = clock_timestamp()
    """), {"adv": advertiser_id, "first": first_count, "latest": latest_count,
           "weeks": weeks_observed, "pct": pct_change})


def upsert_funnel(conn, ad_id, landing_url, final_url_after_redirect, screenshot_s3_key,
                  kind=None, quiz_steps=None) -> None:
    conn.execute(text("""
        INSERT INTO public.funnels
            (ad_id, landing_url, final_url_after_redirect, screenshot_s3_key, kind, quiz_steps)
        VALUES (:ad_id, :landing, :final, :shot, :kind, :quiz_steps)
        ON CONFLICT (ad_id) DO UPDATE SET
          landing_url = EXCLUDED.landing_url,
          final_url_after_redirect = EXCLUDED.final_url_after_redirect,
          screenshot_s3_key = EXCLUDED.screenshot_s3_key,
          kind = EXCLUDED.kind,
          quiz_steps = EXCLUDED.quiz_steps,
          captured_at = now()
    """), {"ad_id": ad_id, "landing": landing_url, "final": final_url_after_redirect,
           "shot": screenshot_s3_key, "kind": kind, "quiz_steps": quiz_steps})


def upsert_advertiser_enrichment(conn, advertiser_id, domain_id=None, parent_company_id=None,
                                 page_category=None, ig_username=None, ig_followers=None) -> None:
    """Plan-6 backfill of advertiser enrichment cols; COALESCE keeps existing on None."""
    conn.execute(text("""
        UPDATE public.advertisers SET
          domain_id         = COALESCE(:domain_id, domain_id),
          parent_company_id = COALESCE(:parent, parent_company_id),
          page_category     = COALESCE(:cat, page_category),
          ig_username       = COALESCE(:ig, ig_username),
          ig_followers      = COALESCE(:igf, ig_followers),
          last_seen         = now()
        WHERE id = :id
    """), {"id": advertiser_id, "domain_id": domain_id, "parent": parent_company_id,
           "cat": page_category, "ig": ig_username, "igf": ig_followers})
