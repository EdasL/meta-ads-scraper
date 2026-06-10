"""The live worker entrypoint: `python -m agf.workers.main`.

Composes the already-tested pieces behind ONE guarded loop —
    build extractor (warms lazily via SessionPool) →
    breadth-first discovery (daily-gated keyword sweep → store-lookup triage) →
    app-store refresh (daily adtech.app_metrics snapshot + metadata + reviews) →
    [optional] run_scoring →
    enrich keepers + flagged gems via run_worker(make_enrich_handler(deps_factory))
— then sleeps and repeats. The loop stays hourly, but the sweep+triage stage is
gated to one finished discovery.sweep_runs row per day.

SAFE BY DEFAULT for dry-runs. With AGF_DRY_RUN=1 it swaps an in-process
FakeExtractor (zero Meta traffic), runs exactly ONE sweep (2 keywords, offline
store lookups, no sweep_runs row), and enriches the just-kept advertisers so
the whole path executes offline. Every sweep is bounded by env caps so a real
run can't hammer Meta.

Env:
  AGF_DATABASE_URL             Postgres DSN (required)
  AGF_PROXIES                  residential proxy pool for the real Extractor
  AGF_DRY_RUN=1                fake extractor + run once, no network  (also: --dry-run / --once)
  AGF_SWEEP_PAGES              SERP pages per sweep keyword          (default 2)
  AGF_KW_DELAY                 seconds between sweep keywords        (default 3.0)
  AGF_DISCOVERY_ENRICH_BUDGET  proxy-request budget for enrich       (default 600 ≈ 42 gems)
  AGF_ENRICH_TOP_N             ads enriched per gem                  (default 5)
  AGF_ENRICH_TTL_HOURS         re-enrich a gem at most this often    (default 168 = 7d)
  AGF_SCORE_AFTER_SWEEP=1      run run_scoring after discovery       (default off; cron owns it)
  AGF_MIN_GEM_SCORE            enrich gems at/above this score       (default 70)
  AGF_SWEEP_INTERVAL_SEC       sleep between sweeps                  (default 3600; ignored if once)
"""
import asyncio
import os
import sys
import time
from datetime import date, datetime, timezone, timedelta
from functools import partial

from sqlalchemy import text

from agf.db.engine import get_engine, begin
from agf.db.candidates import (close_sweep_run, kept_advertiser_ids,
                               open_sweep_run, sweep_ran_today)
from agf.discovery.sweep import run_sweep
from agf.discovery.taxonomy import sweep_entries
from agf.discovery.triage import run_triage
from agf.enrich_full.appstore import lookup_google_play, lookup_itunes_batch
from agf.enrich_full.run_enrich import make_enrich_handler, EnrichDeps
from agf.scoring.run_scoring import run_scoring, ALERT_THRESHOLD
from agf.tracker.snapshots import snapshot_advertiser_week
from agf.workers.runner import run_worker


# --- env helpers -------------------------------------------------------------
def _env_int(name, default):
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_float(name, default):
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_bool(name):
    return os.environ.get(name, "").lower() in ("1", "true", "yes", "on")


# --- extractor + deps wiring -------------------------------------------------
def build_extractor(dry_run, now_epoch):
    """Real Extractor (lazily warms a curl_cffi session via SessionPool +
    ProxyPool.from_env) — or the zero-network fake for a dry-run."""
    if dry_run:
        from agf.extract.fake import FakeExtractor
        return FakeExtractor.demo(now_epoch)
    from agf.extract.facade import Extractor
    return Extractor()


def dry_run_deps_factory(extractor):
    """Enrich deps with no network clients and screenshots off — exercises the
    enrich path (resolve_landing + company) without touching the network."""
    def factory(conn, advertiser_id):
        return EnrichDeps(extractor=extractor, capture=False, creative_urls={})
    return factory


def _demo_lookup(ids):
    """Offline AppMeta for any id — lets a dry-run exercise the full triage path."""
    from agf.enrich import AppMeta
    return {str(i): AppMeta(track_id=str(i), name=f"Demo App {i}", seller="Demo Dev",
                            genre="Health & Fitness", release_date="2026-05-01",
                            rating_count=120, avg_rating=4.8, is_game=False)
            for i in ids}


def prefetch_creative_urls(extractor, conn, advertiser_ids, max_pages: int = 10) -> dict:
    """Fetch current creative URLs per gem via a live page search, OUTSIDE any write
    transaction (fbcdn signed URLs expire, so they must be fresh). This is deliberately
    NOT done inside the per-gem deps_factory: run_worker calls that handler inside an open
    write transaction, and a slow Meta search there would pin a DB connection + hold
    write locks across the network round-trip. max_pages bounds the Meta calls per gem —
    callers that only download media for the top-N ads need far fewer than the default.
    Returns {advertiser_id: {external_ad_id: [(type, url)]}}."""
    out: dict = {}
    for aid in advertiser_ids:
        page_id = conn.execute(text("SELECT page_id FROM public.advertisers WHERE id=:a"),
                               {"a": aid}).scalar()
        if not page_id:
            continue
        ads = asyncio.run(extractor.search(query="", countries=["US"],
                                           search_type="page", view_all_page_id=page_id,
                                           max_pages=max_pages))
        per_ad = {}
        for ad in ads:
            urls = [("image", u) for u in (getattr(ad, "image_urls", None) or [])]
            urls += [("video", u) for u in (getattr(ad, "video_urls", None) or [])]
            if urls:
                per_ad[str(ad.ad_archive_id)] = urls
        if per_ad:
            out[aid] = per_ad
    return out


def production_deps_factory(extractor, http_client, creative_urls_by_adv):
    """Full per-gem deps (RUNBOOK §5) + real iTunes/HTTP/S3/WHOIS clients. `http_client`
    is a sweep-scoped httpx.Client owned (and closed) by main(), shared across gems so
    enrichment doesn't leak a pool per gem. The factory is PURE (no network/DB): creative
    URLs are prefetched out-of-transaction (see prefetch_creative_urls). Heavy deps are
    lazy-imported so a dry-run never needs boto3/whois installed.

    NOTE: enrich_gem itself still does network (resolve_landing/iTunes/funnel-capture/
    creative downloads) inside run_worker's per-gem write txn — that is the Plan 6 §3.9
    handler contract; keep AGF_DISCOVERY_ENRICH_BUDGET modest and avoid a tight
    idle-in-transaction server timeout."""
    from agf.storage.s3 import get_s3, put_bytes
    try:
        import whois as _whois
        whois_fn = _whois.whois
    except Exception:
        whois_fn = None

    def fetch_text(url):
        return http_client.get(url).text

    def fetch_bytes(url):
        r = http_client.get(url)
        return r.content, r.headers.get("content-type", "")

    def factory(conn, advertiser_id):
        return EnrichDeps(extractor=extractor, itunes_client=http_client,
                          fetch_text=fetch_text, fetch_bytes=fetch_bytes,
                          put=put_bytes, s3=get_s3(), whois_fn=whois_fn, page=None,
                          proxy=os.environ.get("AGF_FUNNEL_PROXY"), capture=True,
                          creative_urls=creative_urls_by_adv.get(advertiser_id, {}))
    return factory


# --- queries -----------------------------------------------------------------
def eligible_gem_ids(conn, min_score):
    """Flagged gems (alert + score≥min) — the human-review/enrich set (§3.7)."""
    return [r[0] for r in conn.execute(text(
        "SELECT advertiser_id FROM frontend.emerging_scaling_apps "
        "WHERE alert = TRUE AND gem_score >= :s ORDER BY gem_score DESC"),
        {"s": min_score}).all()]


# --- one sweep ---------------------------------------------------------------
def run_once(engine, extractor, *, now_epoch, as_of, dry_run, http_client=None,
             sweep_pages=2, kw_delay=3.0, enrich_budget=600, enrich_top_n=5,
             enrich_ttl_hours=168, score_after=False, min_score=ALERT_THRESHOLD) -> dict:
    """One sweep→triage→(score)→enrich pass. Returns a summary dict (no printing).
    http_client is the sweep-scoped httpx.Client for the production triage/enrich path
    (None in a dry-run, which uses offline lookups and the no-network fake deps)."""
    # 1) breadth-first discovery — daily-gated. Sweep commits per keyword; triage is
    #    one txn. A dry-run always sweeps (offline) and never records a sweep_runs row.
    sweep_stats, triage_stats = None, None
    with engine.connect() as conn:
        due = dry_run or not sweep_ran_today(conn, as_of)
    if due:
        run_id = None
        if not dry_run:
            with begin(engine) as conn:
                run_id = open_sweep_run(conn)
        try:
            if dry_run:
                keywords = [e.keyword for e in sweep_entries()[:2]]
                sweep_stats = asyncio.run(run_sweep(
                    engine, extractor, keywords=keywords, max_pages=sweep_pages,
                    kw_delay=0, sleep=lambda s: None))
                lookup_ios = lookup_android = _demo_lookup
            else:
                sweep_stats = asyncio.run(run_sweep(
                    engine, extractor, max_pages=sweep_pages, kw_delay=kw_delay))
                lookup_ios = partial(lookup_itunes_batch, http_client, pause=3.0)
                lookup_android = lookup_google_play
            if dry_run or http_client is not None:
                with begin(engine) as conn:
                    triage_stats = run_triage(conn, lookup_ios=lookup_ios,
                                              lookup_android=lookup_android)
            if run_id is not None:
                with begin(engine) as conn:
                    close_sweep_run(conn, run_id, sweep=sweep_stats, triage=triage_stats)
        except Exception as exc:
            if run_id is not None:
                with begin(engine) as conn:
                    close_sweep_run(conn, run_id, sweep=sweep_stats,
                                    triage=triage_stats, error=repr(exc))
            raise

    # 1.5) velocity tracker — roll THIS week's active-ad counts into the weekly
    #      app_advertiser_metrics series that build_features/GemScore read. Nothing else
    #      calls this in production, so without it the week-over-week signal never builds,
    #      scaling_velocity stays flat, and no advertiser can EVER cross the alert
    #      threshold no matter how long the system runs. Pure DB (counts public.ads), so
    #      it adds no Meta traffic. Global rollup: every tracked advertiser must accrue
    #      the weekly series (the EU confirm-set that used to scope it no longer exists).
    week_start = as_of - timedelta(days=as_of.weekday())
    with begin(engine) as conn:
        snapshot_weeks = snapshot_advertiser_week(conn, week_start, only_ids=None)

    # 1.6) resolve metadata for newly-linked apps (name/genre/release/ratings). Cheap
    #      iTunes/gplay lookups (no Meta traffic), so every sweep makes discovered apps
    #      interpretable AND feeds genre (cohort), release_date (emerging gate), and
    #      rating_count (under_radar) into scoring for ALL advertisers — not just enriched
    #      gems. Production only (needs the sweep's HTTP client); idempotent.
    if not dry_run and http_client is not None:
        from agf.enrich_full.appstore import resolve_pending_app_metadata
        with begin(engine) as conn:
            resolve_pending_app_metadata(conn, http_client)

    # 1.7) app-store refresh — daily per-app signals where the ad snapshots roll up:
    #      store metadata (icon/screenshots/price/installs), today's adtech.app_metrics
    #      snapshot row (the app-side growth series), and the ~20 most-recent reviews.
    #      Scoped to apps missing today's snapshot, so an hourly sweep cadence still
    #      hits the store APIs at most once per app per day. Production only (needs the
    #      sweep's HTTP client); Apple/Google endpoints — zero Meta traffic.
    apps_refreshed = 0
    if not dry_run and http_client is not None:
        from agf.enrich_full.app_refresh import apps_due_for_refresh, refresh_apps
        with engine.connect() as conn:
            due = apps_due_for_refresh(conn, as_of)
        if due:
            refreshed = refresh_apps(engine, only_ids=due, itunes_client=http_client,
                                     snapshot_date=as_of)
            apps_refreshed = refreshed["apps"]
        # retention: the app-side history is the growth signal, so the window is
        # conservative (default 2 years, AGF_APP_HISTORY_KEEP_DAYS to override).
        from agf.db.repo import prune_app_history
        keep_days = int(os.environ.get("AGF_APP_HISTORY_KEEP_DAYS", "730"))
        with begin(engine) as conn:
            prune_app_history(conn, keep_days=keep_days)

    # 2) scoring — optional; its own committed txn + CONCURRENT matview refresh
    scored = run_scoring(engine, as_of) if score_after else None

    # 3) enrich — keepers first (they have no ad history yet, so they can't score
    #    until enriched), then flagged gems due for their TTL refresh; both under a
    #    proxy-request budget instead of a fixed count. ~14 Meta requests per gem
    #    (12 history pages + creative prefetch). One transaction per gem (run_worker,
    #    source=engine); creative URLs are prefetched on a READ connection first.
    est_requests_per_gem = 14
    enrich_limit = max(1, enrich_budget // est_requests_per_gem)
    with engine.connect() as conn:
        kept = kept_advertiser_ids(conn)
        cand_ids = kept if dry_run else list(dict.fromkeys(
            kept + eligible_gem_ids(conn, min_score)))
        if dry_run:
            deps_factory = dry_run_deps_factory(extractor)
        else:
            creative_urls = prefetch_creative_urls(extractor, conn, cand_ids[:enrich_limit])
            deps_factory = production_deps_factory(extractor, http_client, creative_urls)
    due_before = datetime.now(timezone.utc) - timedelta(hours=enrich_ttl_hours)
    enriched = run_worker("enrich", "advertiser",
                          make_enrich_handler(deps_factory, top_n=enrich_top_n),
                          source=engine, candidate_ids=cand_ids,
                          due_before=due_before, limit=enrich_limit)
    return {"sweep": sweep_stats, "triage": triage_stats,
            "snapshot_weeks": snapshot_weeks, "apps_refreshed": apps_refreshed,
            "scored": scored, "enrich_candidates": cand_ids, "enriched": enriched}


def _heartbeat_path() -> str:
    return os.environ.get("AGF_HEARTBEAT_FILE", "/tmp/agf-worker.heartbeat")


def _touch_heartbeat() -> None:
    try:
        with open(_heartbeat_path(), "w") as fh:
            fh.write(str(int(time.time())))
    except OSError:
        pass


def _healthcheck() -> int:
    """Exit 0 iff the worker wrote a heartbeat within 2 sweeps + 5 min — so a hung or
    crash-looping worker (the old print-and-exit stub failure mode) reads as down."""
    path = _heartbeat_path()
    interval = _env_int("AGF_SWEEP_INTERVAL_SEC", 3600)
    if not os.path.exists(path):
        return 1
    return 0 if (time.time() - os.path.getmtime(path)) < (2 * interval + 300) else 1


def _summary_line(s: dict) -> str:
    sw, tr = s.get("sweep"), s.get("triage")
    sweep_part = (f"new={sw['candidates_new']} seen={sw['candidates_seen']} "
                  f"kw={sw['keywords_swept']}" if sw else "skipped(daily-gate)")
    kept = tr["kept"] if tr else 0
    line = (f"[worker] sweep: {sweep_part} kept={kept} "
            f"weekly_snapshots={s['snapshot_weeks']} "
            f"apps_refreshed={s.get('apps_refreshed', 0)} "
            f"enriched={len(s['enriched'])}/{len(s['enrich_candidates'])}")
    if s["scored"] is not None:
        line += f" scored={s['scored']}"
    return line


def main(argv=None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if "--healthcheck" in argv:
        sys.exit(_healthcheck())
    dry_run = _env_bool("AGF_DRY_RUN") or "--dry-run" in argv
    once = dry_run or "--once" in argv
    engine = get_engine()
    cfg = dict(
        sweep_pages=_env_int("AGF_SWEEP_PAGES", 2),
        kw_delay=_env_float("AGF_KW_DELAY", 3.0),
        enrich_budget=_env_int("AGF_DISCOVERY_ENRICH_BUDGET", 600),
        enrich_top_n=_env_int("AGF_ENRICH_TOP_N", 5),
        enrich_ttl_hours=_env_int("AGF_ENRICH_TTL_HOURS", 168),
        score_after=_env_bool("AGF_SCORE_AFTER_SWEEP"),
        min_score=_env_float("AGF_MIN_GEM_SCORE", ALERT_THRESHOLD),
    )
    interval = _env_int("AGF_SWEEP_INTERVAL_SEC", 3600)
    mode = "DRY-RUN (fake extractor, no Meta traffic)" if dry_run else "LIVE"
    print(f"[worker] starting — mode={mode} once={once} caps={cfg}", flush=True)
    _touch_heartbeat()                                # healthy before the first (slow) sweep

    while True:
        now_epoch = int(time.time())
        extractor = build_extractor(dry_run, now_epoch)
        http_client = None
        if not dry_run:
            import httpx
            http_client = httpx.Client(timeout=20, follow_redirects=True)  # one per sweep
        try:
            summary = run_once(engine, extractor, now_epoch=now_epoch,
                               as_of=date.today(), dry_run=dry_run,
                               http_client=http_client, **cfg)
            print(_summary_line(summary), flush=True)
        except Exception as exc:                      # one bad sweep must not kill the loop
            print(f"[worker] sweep failed: {exc!r}", file=sys.stderr, flush=True)
            if once:
                raise
        finally:
            if http_client is not None:
                http_client.close()
        _touch_heartbeat()
        if once:
            break
        time.sleep(interval)


if __name__ == "__main__":
    main()
