"""Daily app-side refresh (gem-signals spec, Layer 1 item 3).

For every app (or `only_ids` — which DB-backed tests MUST use): re-fetch store
metadata (BATCH iTunes lookups / google-play sidecar), update `public.apps`
(icon/screenshots/price/installs/ratings + updated_at), insert today's
`adtech.app_metrics` snapshot row (rating, rating_count, installs — the app-side
growth series), and refresh the ~20 most-recent reviews into `public.app_reviews`.

Idempotent per day: every write upserts on its PK, so a re-run within the same day
only re-touches the same rows. Network fetches happen OUTSIDE write transactions
(same discipline as prefetch_creative_urls). Zero Meta traffic — Apple/Google
endpoints only, no proxy needed."""
import time
from datetime import date

from sqlalchemy import text

from agf.db import repo
from agf.db.engine import begin
from agf.enrich_full.appstore import lookup_itunes_batch, lookup_google_play
from agf.enrich_full.reviews import fetch_itunes_reviews, fetch_gplay_reviews


def apps_due_for_refresh(conn, as_of=None) -> list[int]:
    """App ids with no adtech.app_metrics row for `as_of` (default today). The worker
    scopes each sweep to this set, so an hourly sweep cadence still hits the store
    APIs at most once per app per day."""
    return [r[0] for r in conn.execute(text("""
        SELECT a.id FROM public.apps a
        LEFT JOIN adtech.app_metrics m ON m.app_id = a.id AND m.snapshot_date = :d
        WHERE m.app_id IS NULL
        ORDER BY a.id
    """), {"d": as_of or date.today()}).all()]


def _meta_row(store, meta) -> dict:
    return {"store": store, "store_id": meta.track_id, "name": meta.name,
            "developer": meta.seller, "genre": meta.genre,
            "release_date": meta.release_date,
            "rating": meta.avg_rating or None, "rating_count": meta.rating_count,
            "installs": meta.installs, "icon_url": meta.icon_url,
            "screenshots": meta.screenshots, "price_label": meta.price_label,
            "description": meta.description}


def refresh_apps(engine, only_ids=None, *, itunes_client=None, gp_runner=None,
                 gp_reviews_runner=None, snapshot_date=None, reviews_per_app=20,
                 pause=3.0, sleep=time.sleep) -> dict:
    """Refresh store metadata + daily metric snapshot + recent reviews for every app
    (or `only_ids`). Returns {"apps": n, "metrics": n, "reviews": n}.

    `itunes_client` is an httpx-like client (a private one is opened/closed when None);
    `gp_runner` / `gp_reviews_runner` inject the Node sidecars for tests. `pause`
    seconds space the iTunes batch calls and per-app review fetches (~20 calls/min
    Lookup limit; reviews RSS gets the same politeness)."""
    snap = snapshot_date or date.today()
    sql = "SELECT id, store, store_id FROM public.apps"
    params: dict = {}
    if only_ids is not None:
        sql += " WHERE id = ANY(:ids)"
        params["ids"] = list(only_ids)
    with engine.connect() as conn:
        rows = conn.execute(text(sql + " ORDER BY id"), params).all()

    ios = [(app_id, str(sid)) for app_id, store, sid in rows if store == "ios"]
    android = [(app_id, str(sid)) for app_id, store, sid in rows if store == "android"]

    own_client = itunes_client is None and bool(ios)
    client = itunes_client
    if own_client:
        import httpx
        client = httpx.Client(timeout=30, follow_redirects=True)

    n_apps = n_metrics = n_reviews = 0
    try:
        # --- metadata (network, no txn open) ----------------------------------
        itunes_meta = {}
        if ios:
            try:
                itunes_meta = lookup_itunes_batch(
                    client, [sid for _, sid in ios], pause=pause, sleep=sleep)
            except Exception:
                itunes_meta = {}                       # store hiccup → skip this batch
        gplay_meta = {}
        if android:
            try:
                gplay_meta = lookup_google_play(
                    [sid for _, sid in android], runner=gp_runner)
            except Exception:
                gplay_meta = {}

        # --- apps + daily metric snapshot (one committed txn) ------------------
        with begin(engine) as conn:
            for app_id, store, sid in rows:
                meta = (itunes_meta if store == "ios" else gplay_meta).get(str(sid))
                if not meta:
                    continue
                repo.upsert_app(conn, _meta_row(store, meta))
                repo.upsert_app_metric(conn, app_id, snap, meta.avg_rating or None,
                                       meta.rating_count, meta.installs)
                n_apps += 1
                n_metrics += 1

        # --- reviews (fetch outside txn; small txn per app; degrade per app) ---
        first_review_fetch = True
        for app_id, store, sid in rows:
            meta = (itunes_meta if store == "ios" else gplay_meta).get(str(sid))
            if not meta:
                continue                               # unknown id → no feed either
            if not first_review_fetch and pause:
                sleep(pause)
            first_review_fetch = False
            try:
                if store == "ios":
                    reviews = fetch_itunes_reviews(client, sid, limit=reviews_per_app)
                else:
                    reviews = fetch_gplay_reviews(sid, num=reviews_per_app,
                                                  runner=gp_reviews_runner)
            except Exception:
                reviews = []                           # empty/missing feeds are fine
            if reviews:
                with begin(engine) as conn:
                    n_reviews += repo.upsert_app_reviews(conn, app_id, reviews)
    finally:
        if own_client:
            client.close()
    return {"apps": n_apps, "metrics": n_metrics, "reviews": n_reviews}
