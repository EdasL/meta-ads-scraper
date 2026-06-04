"""conn-first data-access (master-plan §3.3). Every function takes a SQLAlchemy
Connection as its first arg and runs inside the caller's transaction (no commits)."""
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


def _app_dict(meta) -> dict:
    """Accept either a dict of app columns or a Plan-1 AppMeta (iOS)."""
    if isinstance(meta, dict):
        m = dict(meta)
    elif hasattr(meta, "track_id"):       # Plan 1 enrich.AppMeta
        m = {"store": "ios", "store_id": meta.track_id, "name": meta.name,
             "developer": getattr(meta, "seller", None), "genre": meta.genre,
             "release_date": meta.release_date, "rating": getattr(meta, "avg_rating", None),
             "rating_count": meta.rating_count}
    else:
        raise TypeError("upsert_app expects a dict or AppMeta")
    if not m.get("store") or not m.get("store_id"):
        raise ValueError("upsert_app needs store + store_id")
    return m


# --- core upserts ------------------------------------------------------------
def upsert_advertiser(conn, ar) -> int:
    """Minimal advertiser insert (page_id, page_name). Enrichment cols are Plan-6."""
    return conn.execute(text("""
        INSERT INTO public.advertisers (page_id, page_name)
        VALUES (:page_id, :page_name)
        ON CONFLICT (page_id) DO UPDATE
          SET page_name = COALESCE(EXCLUDED.page_name, public.advertisers.page_name),
              last_seen = now()
        RETURNING id
    """), {"page_id": ar.page_id, "page_name": getattr(ar, "page_name", None)}).scalar_one()


def upsert_app(conn, meta) -> int:
    m = _app_dict(meta)
    return conn.execute(text("""
        INSERT INTO public.apps
          (store, store_id, name, developer, genre, release_date, rating,
           rating_count, installs, free, icon_url, is_emerging)
        VALUES
          (:store, :store_id, :name, :developer, :genre, :release_date, :rating,
           :rating_count, :installs, :free, :icon_url, :is_emerging)
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
          is_emerging  = COALESCE(EXCLUDED.is_emerging, public.apps.is_emerging),
          updated_at   = now()
        RETURNING id
    """), {
        "store": m["store"], "store_id": str(m["store_id"]), "name": m.get("name"),
        "developer": m.get("developer"), "genre": m.get("genre"),
        "release_date": _as_date(m.get("release_date")), "rating": m.get("rating"),
        "rating_count": m.get("rating_count"), "installs": m.get("installs"),
        "free": m.get("free"), "icon_url": m.get("icon_url"),
        "is_emerging": m.get("is_emerging"),
    }).scalar_one()


def upsert_ad(conn, ar, advertiser_id: int, app_id: int | None) -> int:
    return conn.execute(text("""
        INSERT INTO public.ads
          (external_ad_id, advertiser_id, app_id, link_url, cta_type, cta_text,
           publisher_platforms, is_active, start_date, end_date, collation_count)
        VALUES
          (:ext, :adv, :app, :link, :cta_type, :cta_text,
           :platforms, :is_active, :start_date, :end_date, :collation)
        ON CONFLICT (external_ad_id) DO UPDATE SET
          advertiser_id   = EXCLUDED.advertiser_id,
          app_id          = COALESCE(EXCLUDED.app_id, public.ads.app_id),
          link_url        = COALESCE(EXCLUDED.link_url, public.ads.link_url),
          cta_type        = COALESCE(EXCLUDED.cta_type, public.ads.cta_type),
          cta_text        = COALESCE(EXCLUDED.cta_text, public.ads.cta_text),
          is_active       = EXCLUDED.is_active,
          end_date        = COALESCE(EXCLUDED.end_date, public.ads.end_date),
          collation_count = COALESCE(EXCLUDED.collation_count, public.ads.collation_count),
          last_seen       = now()
        RETURNING id
    """), {
        "ext": str(ar.ad_archive_id), "adv": advertiser_id, "app": app_id,
        "link": ar.link_url, "cta_type": ar.cta_type, "cta_text": ar.cta_text,
        "platforms": getattr(ar, "publisher_platforms", None),
        "is_active": bool(ar.is_active), "start_date": _ts(ar.start_date),
        "end_date": _ts(ar.end_date), "collation": ar.collation_count,
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


# --- metrics / enrichment ----------------------------------------------------
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


def upsert_funnel(conn, ad_id, landing_url, final_url_after_redirect, screenshot_s3_key) -> None:
    conn.execute(text("""
        INSERT INTO public.funnels (ad_id, landing_url, final_url_after_redirect, screenshot_s3_key)
        VALUES (:ad_id, :landing, :final, :shot)
        ON CONFLICT (ad_id) DO UPDATE SET
          landing_url = EXCLUDED.landing_url,
          final_url_after_redirect = EXCLUDED.final_url_after_redirect,
          screenshot_s3_key = EXCLUDED.screenshot_s3_key,
          captured_at = now()
    """), {"ad_id": ad_id, "landing": landing_url, "final": final_url_after_redirect,
           "shot": screenshot_s3_key})


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
