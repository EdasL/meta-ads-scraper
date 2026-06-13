"""The live worker entrypoint: `python -m agf.workers.main`.

Composes the already-tested pieces behind ONE guarded loop —
    build extractor (warms lazily via SessionPool) →
    breadth-first discovery (keyword sweep → store-lookup triage →
    keeper-history backfill: store keeper ads + reconstruct the weekly series) →
    app-store refresh (daily adtech.app_metrics snapshot + metadata + reviews) →
    daily funnel stage (funnel_phrase sweep → confirm/label landing pages) →
    daily keyword flywheel (mine gem ad copy → trial-sweep dynamic keywords) →
    [optional] run_scoring →
    enrich keepers + flagged gems via run_worker(make_enrich_handler(deps_factory))
— then sleeps briefly and repeats. By default (AGF_CONTINUOUS_DISCOVERY=1) the
sweep+triage stage runs every tick so the brand DB grows continuously; set it to 0
for the legacy cadence of one finished discovery.sweep_runs row per day.

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
  AGF_DISCOVERY_ENRICH_BUDGET  proxy-request budget, split in half: keeper-history
                               backfill gets budget//2, enrich the rest (default 600)
  AGF_ENRICH_TOP_N             ads enriched per gem                  (default 5)
  AGF_MEDIA_CAP                media downloads per gem beyond top-N  (default 120)
  AGF_ENRICH_TTL_HOURS         re-enrich a gem at most this often    (default 168 = 7d)
  AGF_SCORE_AFTER_SWEEP=1      run run_scoring after discovery       (default off; cron owns it)
  AGF_MIN_GEM_SCORE            enrich gems at/above this score       (default 70)
  AGF_SWEEP_INTERVAL_SEC       sleep between sweeps                  (default 3600; ignored if once)
  AGF_FUNNEL_DAILY             run the daily funnel sweep+confirm    (default 1; 0 disables)
  AGF_FUNNEL_MAX_PAGES         SERP pages per funnel keyword         (default 3)
  AGF_FUNNEL_CONFIRM_LIMIT     funnel candidates confirmed per day   (default 50)
  AGF_FLYWHEEL_DAILY           run the daily keyword flywheel        (default 1; 0 disables)
  AGF_FLYWHEEL_EVERY_SEC        flywheel cadence in seconds           (default 0 = once/day)
  AGF_FLYWHEEL_MAX_PAGES       SERP pages per dynamic keyword        (default 2)
  AGF_FLYWHEEL_MAX_TRIALS      trial keywords live at once           (default 40)
  AGF_FLYWHEEL_TRIAL_SWEEPS     sweeps before a trial gets a verdict  (default 4)
  AGF_FLYWHEEL_PROMOTE_MIN_NEW  new candidates to promote a trial     (default 3)
  AGF_MINE_MIN_ADVERTISERS      distinct gems a phrase must span      (default 3)
  AGF_MINE_MIN_SCORE           mine ad copy of gems at/above score   (default 40)
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
                               open_sweep_run, reap_open_runs, sweep_ran_today)
from agf.db.dynamic_keywords import (add_trial_keywords, apply_verdicts,
                                     close_flywheel_run, flywheel_ran_today,
                                     flywheel_ran_within, live_keywords,
                                     open_flywheel_run, record_keyword_sweep,
                                     tried_keywords, trial_count)
from agf.db.funnel_candidates import (close_funnel_sweep_run, funnel_sweep_ran_today,
                                      open_funnel_sweep_run)
from agf.discovery.funnel_confirm import run_funnel_confirm
from agf.discovery.keyword_mining import fetch_gem_copy, mine_phrases
from agf.discovery.funnel_sweep import run_funnel_sweep
from agf.discovery.keeper_history import pull_keeper_history
from agf.discovery.sweep import run_sweep
from agf.discovery.taxonomy import TaxonomyEntry, all_entries, sweep_entries
from agf.discovery.triage import run_triage
from agf.enrich_full.appstore import lookup_google_play, lookup_itunes_batch
from agf.enrich_full.media_repair import (repair_advertiser,
                                          textless_feed_advertisers)
from agf.enrich_full.run_enrich import make_enrich_handler, EnrichDeps
from agf.extract.errors import ExtractError
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


def _env_bool(name, default=False):
    v = os.environ.get(name)
    if v is None:
        return default
    return v.lower() in ("1", "true", "yes", "on")


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
        # Per-gem isolation: a page whose search exhausts the facade's retries (hard
        # proxy timeout) costs that gem's media this pass, not the whole batch.
        try:
            # active_status="ALL": ended ads often still serve media for a while —
            # the media-only enrich pass needs their URLs too, same page budget.
            ads = asyncio.run(extractor.search(query="", countries=["US"],
                                               search_type="page", view_all_page_id=page_id,
                                               active_status="ALL", max_pages=max_pages))
        except ExtractError as exc:
            print(f"[worker] creative prefetch failed for advertiser {aid}: {exc}",
                  file=sys.stderr, flush=True)
            continue
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
def discovery_due(conn, as_of, *, dry_run, continuous) -> bool:
    """Whether this tick runs a discovery sweep. A dry-run always sweeps (offline,
    no sweep_runs row). Continuous mode (AGF_CONTINUOUS_DISCOVERY) bypasses the
    once-per-day gate so the brand DB keeps growing every tick. Otherwise gate on a
    finished discovery.sweep_runs row for `as_of` (one real sweep per day)."""
    return dry_run or continuous or not sweep_ran_today(conn, as_of)


def funnel_due(conn, as_of, *, dry_run) -> bool:
    """Whether this tick runs the funnel stage. Once per day (a FINISHED
    discovery.funnel_sweep_runs row gates the rest of the day); never in a dry-run
    (the confirm stage renders real landing pages — there is no offline fake)."""
    return not dry_run and not funnel_sweep_ran_today(conn, as_of)


# --- funnel stage --------------------------------------------------------------
def run_funnel_stage(engine, extractor, *, max_pages=3, kw_delay=3.0,
                     confirm_limit=50, resolve=None, probe=None) -> dict:
    """One funnel sweep+confirm pass — the web2app sibling of the app discovery stage.
    Sweep: the 28-keyword funnel_phrase layer -> discovery.funnel_candidates (Meta via
    the proxied extractor). Confirm: render each new landing page DIRECT (no proxy,
    iOS UA — see funnel_capture) + quiz click-probe + resolve the deeplink, write the
    verdict. `resolve`/`probe` are injectable for tests; production wires the extractor's
    deeplink resolver and the lazy-imported Playwright render_and_probe."""
    if resolve is None:
        def resolve(url):
            return asyncio.run(extractor.resolve_deeplink(url)).intent
    if probe is None:
        from agf.enrich_full.funnel_capture import render_and_probe
        probe = render_and_probe
    sweep = asyncio.run(run_funnel_sweep(engine, extractor,
                                         max_pages=max_pages, kw_delay=kw_delay))
    confirm = run_funnel_confirm(engine, resolve=resolve, probe=probe,
                                 limit=confirm_limit)
    return {"sweep": sweep, "confirm": confirm}


def flywheel_due(conn, as_of, *, dry_run) -> bool:
    """Whether this tick runs the keyword flywheel. Never in a dry-run. With
    AGF_FLYWHEEL_EVERY_SEC set, runs on that interval (several times/day) so the
    trial pool turns over quickly; otherwise falls back to once-per-calendar-day
    (a FINISHED discovery.flywheel_runs row gates the rest of the day)."""
    if dry_run:
        return False
    every = _env_int("AGF_FLYWHEEL_EVERY_SEC", 0)
    if every > 0:
        return not flywheel_ran_within(conn, every)
    return not flywheel_ran_today(conn, as_of)


# --- keyword flywheel ------------------------------------------------------------
def run_flywheel_stage(engine, extractor, *, max_pages=2, kw_delay=3.0,
                       mine_min_score=40.0, max_trials=40, min_advertisers=3,
                       corpus_fn=fetch_gem_copy) -> dict:
    """One mine+sweep pass of the keyword flywheel: phrases recurring across the ad
    copy of scored gems become trial sweep keywords, every live (trial|active) dynamic
    keyword gets one shallow exact-phrase sweep into the SAME app candidate pool the
    static taxonomy feeds, and each trial's yield decides its fate (promote/retire —
    policy constants in db/dynamic_keywords). A throttled keyword (search error)
    doesn't burn trial budget. `corpus_fn` is injectable for tests."""
    # Apply verdicts up front so a lowered AGF_FLYWHEEL_TRIAL_SWEEPS frees the trial
    # pool on this run (trials already past the threshold promote/retire immediately)
    # rather than waiting for each to be re-swept.
    with begin(engine) as conn:
        apply_verdicts(conn)
    with engine.connect() as conn:
        tried = tried_keywords(conn)
        room = max_trials - trial_count(conn)
        corpus = corpus_fn(conn, min_score=mine_min_score) if room > 0 else []
    static = {e.keyword for e in all_entries()}
    mined = mine_phrases(corpus, exclude=static | tried, limit=room,
                         min_advertisers=min_advertisers) if corpus else []
    if mined:
        with begin(engine) as conn:
            add_trial_keywords(conn, mined)
    with engine.connect() as conn:
        live = live_keywords(conn)
    stats = {"mined": mined, "keywords_swept": 0, "errors": 0,
             "candidates_new": 0, "candidates_seen": 0, "promoted": 0, "retired": 0}
    for kw in live:
        entry = TaxonomyEntry(kw.keyword, "flywheel", "KEYWORD_EXACT_PHRASE", max_pages)
        s = asyncio.run(run_sweep(engine, extractor, entries=[entry],
                                  max_pages=max_pages, kw_delay=kw_delay))
        if s["keywords_swept"] != 1:                  # throttled — no verdict from this
            stats["errors"] += 1
            continue
        with begin(engine) as conn:
            status = record_keyword_sweep(conn, kw.id,
                                          new_candidates=s["candidates_new"])
        stats["keywords_swept"] += 1
        stats["candidates_new"] += s["candidates_new"]
        stats["candidates_seen"] += s["candidates_seen"]
        if kw.status == "trial" and status == "active":
            stats["promoted"] += 1
        elif status == "retired":
            stats["retired"] += 1
    return stats


def eligible_gem_ids(conn, min_score):
    """Flagged gems (alert + score≥min) — the human-review/enrich set (§3.7)."""
    return [r[0] for r in conn.execute(text(
        "SELECT advertiser_id FROM frontend.emerging_scaling_apps "
        "WHERE alert = TRUE AND gem_score >= :s ORDER BY gem_score DESC"),
        {"s": min_score}).all()]


# --- one sweep ---------------------------------------------------------------
def run_once(engine, extractor, *, now_epoch, as_of, dry_run, http_client=None,
             sweep_pages=2, kw_delay=3.0, enrich_budget=600, enrich_top_n=5,
             enrich_media_cap=120, enrich_ttl_hours=168, score_after=False,
             min_score=ALERT_THRESHOLD, continuous_discovery=False,
             funnel_daily=True, funnel_pages=3, funnel_confirm_limit=50,
             flywheel_daily=True, flywheel_pages=2, flywheel_max_trials=40,
             flywheel_min_advertisers=3, mine_min_score=40.0,
             media_repair_per_cycle=5) -> dict:
    """One sweep→triage→(score)→enrich pass. Returns a summary dict (no printing).
    http_client is the sweep-scoped httpx.Client for the production triage/enrich path
    (None in a dry-run, which uses offline lookups and the no-network fake deps)."""
    # 1) breadth-first discovery. Default cadence is one finished sweep_runs row per day
    #    (sweep_ran_today gate); continuous_discovery (AGF_CONTINUOUS_DISCOVERY) bypasses
    #    that gate so a fresh sweep runs every loop tick — the production setting for
    #    building out the brand DB. Sweep commits per keyword; triage is one txn. A
    #    dry-run always sweeps (offline) and never records a sweep_runs row.
    sweep_stats, triage_stats = None, None
    with engine.connect() as conn:
        sweep_due = discovery_due(conn, as_of, dry_run=dry_run,
                                  continuous=continuous_discovery)
    if sweep_due:
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
    _touch_heartbeat()  # sweep is the long stage; prove liveness before the rest

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

    # 1.8) keeper history — triage promotes keepers with advertiser+app rows but ZERO
    #      ads, so their weekly series in adtech.app_advertiser_metrics never builds and
    #      they can never pass the scoring persistence gate (weeks_observed >= 2). One
    #      page-scoped active_status=ALL pull per keeper stores its ads and reconstructs
    #      the last 16 Monday-aligned weeks (the proven scripts/persist_candidates.py
    #      path). Same daily gate as the sweep; spends half the discovery enrich budget —
    #      step 3 keeps the other half.
    keeper_history = None
    if sweep_due:
        keeper_history = asyncio.run(pull_keeper_history(
            engine, extractor, budget_requests=enrich_budget // 2,
            sleep=(lambda s: None) if dry_run else time.sleep,
            kw_delay=0.0 if dry_run else 2.0))

    # 1.9) funnel pipeline — daily web2app-funnel accrual, sibling to the app sweep
    #      (HANDOFF-funnel-pipeline roadmap §2). Gated to one finished
    #      discovery.funnel_sweep_runs row per day, so the continuous loop still hits
    #      the funnel_phrase keywords once daily. Failures are recorded on the run row
    #      and must NOT cost the app pipeline the rest of this tick.
    funnel = None
    if funnel_daily:
        with engine.connect() as conn:
            f_due = funnel_due(conn, as_of, dry_run=dry_run)
        if f_due:
            with begin(engine) as conn:
                f_run_id = open_funnel_sweep_run(conn)
            try:
                funnel = run_funnel_stage(engine, extractor, max_pages=funnel_pages,
                                          kw_delay=kw_delay,
                                          confirm_limit=funnel_confirm_limit)
                with begin(engine) as conn:
                    close_funnel_sweep_run(conn, f_run_id, stats=funnel)
            except Exception as exc:
                with begin(engine) as conn:
                    close_funnel_sweep_run(conn, f_run_id, error=repr(exc))
                print(f"[worker] funnel stage failed: {exc!r}", file=sys.stderr,
                      flush=True)
        _touch_heartbeat()  # funnel renders landing pages — another long, blocking stage

    # 1.95) keyword flywheel — daily self-expansion of the discovery surface: mine
    #       recurring phrases from scored gems' ad copy into trial keywords, sweep
    #       every live dynamic keyword shallow, promote earners / retire duds.
    #       Same isolation contract as the funnel stage: failures land on the run
    #       row, never on the rest of this tick.
    flywheel = None
    if flywheel_daily:
        with engine.connect() as conn:
            fw_due = flywheel_due(conn, as_of, dry_run=dry_run)
        if fw_due:
            with begin(engine) as conn:
                fw_run_id = open_flywheel_run(conn)
            try:
                flywheel = run_flywheel_stage(engine, extractor,
                                              max_pages=flywheel_pages,
                                              kw_delay=kw_delay,
                                              mine_min_score=mine_min_score,
                                              max_trials=flywheel_max_trials,
                                              min_advertisers=flywheel_min_advertisers)
                with begin(engine) as conn:
                    close_flywheel_run(conn, fw_run_id, stats=flywheel)
            except Exception as exc:
                with begin(engine) as conn:
                    close_flywheel_run(conn, fw_run_id, error=repr(exc))
                print(f"[worker] flywheel stage failed: {exc!r}", file=sys.stderr,
                      flush=True)

    # 2) scoring — optional; its own committed txn + CONCURRENT matview refresh
    scored = run_scoring(engine, as_of) if score_after else None

    # 3) enrich — keepers first (they have no ad history yet, so they can't score
    #    until enriched), then flagged gems due for their TTL refresh; both under a
    #    proxy-request budget instead of a fixed count. ~14 Meta requests per gem
    #    (12 history pages + creative prefetch). One transaction per gem (run_worker,
    #    source=engine); creative URLs are prefetched on a READ connection first.
    est_requests_per_gem = 14
    enrich_limit = max(1, (enrich_budget - enrich_budget // 2) // est_requests_per_gem)
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
                          make_enrich_handler(deps_factory, top_n=enrich_top_n,
                                              media_cap=enrich_media_cap),
                          source=engine, candidate_ids=cand_ids,
                          due_before=due_before, limit=enrich_limit)
    _touch_heartbeat()  # enrich is long; prove liveness before media repair

    # 3.5) media repair — drain the textless-feed backlog a few advertisers per
    #      cycle so a gem's active ads can't sit media-less forever (the enrich
    #      pass only fetches the top-N of the ACTIVE listing). Bounded + isolated:
    #      never in a dry run, failures don't abort the cycle. 0 disables.
    media_repair = {}
    if not dry_run and media_repair_per_cycle > 0:
        try:
            with engine.connect() as conn:
                repair_ids = textless_feed_advertisers(conn, media_repair_per_cycle)
            for aid in repair_ids:
                repair_advertiser(engine, extractor, aid, media_repair)
        except Exception as exc:  # noqa: BLE001 — repair must not kill the cycle
            print(f"[worker] media repair failed: {exc!r}", file=sys.stderr, flush=True)
            media_repair["error"] = repr(exc)
    return {"sweep": sweep_stats, "triage": triage_stats,
            "keeper_history": keeper_history, "funnel": funnel, "flywheel": flywheel,
            "snapshot_weeks": snapshot_weeks, "apps_refreshed": apps_refreshed,
            "scored": scored, "enrich_candidates": cand_ids, "enriched": enriched,
            "media_repair": media_repair}


def _heartbeat_path() -> str:
    return os.environ.get("AGF_HEARTBEAT_FILE", "/tmp/agf-worker.heartbeat")


def _touch_heartbeat() -> None:
    try:
        with open(_heartbeat_path(), "w") as fh:
            fh.write(str(int(time.time())))
    except OSError:
        pass


def _healthcheck() -> int:
    """Exit 0 iff the worker wrote a heartbeat recently. A full sweep→funnel→enrich
    cycle can take ~40 min, so the staleness budget must clear one whole cycle plus
    slack — otherwise a healthy worker mid-sweep reads as down (the original bug).
    run_once touches the heartbeat at each stage boundary, so a genuinely hung or
    crash-looping worker still trips this. Tune via AGF_HEALTHCHECK_MAX_STALE_SEC."""
    path = _heartbeat_path()
    interval = _env_int("AGF_SWEEP_INTERVAL_SEC", 3600)
    max_stale = max(_env_int("AGF_HEALTHCHECK_MAX_STALE_SEC", 3600), 2 * interval + 300)
    if not os.path.exists(path):
        return 1
    return 0 if (time.time() - os.path.getmtime(path)) < max_stale else 1


def _summary_line(s: dict) -> str:
    sw, tr = s.get("sweep"), s.get("triage")
    sweep_part = (f"new={sw['candidates_new']} seen={sw['candidates_seen']} "
                  f"kw={sw['keywords_swept']}" if sw else "skipped(daily-gate)")
    kept = tr["kept"] if tr else 0
    line = f"[worker] sweep: {sweep_part} kept={kept}"
    if s.get("keeper_history") is not None:
        line += f" hist={s['keeper_history']['keepers_done']}"
    if s.get("funnel") is not None:
        fs, fco = s["funnel"]["sweep"], s["funnel"]["confirm"]
        line += (f" funnel(new={fs['candidates_new']} seen={fs['candidates_seen']} "
                 f"confirmed={fco['confirmed']}/{fco['examined']})")
    if s.get("flywheel") is not None:
        fw = s["flywheel"]
        line += (f" flywheel(kw={fw['keywords_swept']} mined={len(fw['mined'])} "
                 f"new={fw['candidates_new']})")
    line += (f" weekly_snapshots={s['snapshot_weeks']} "
            f"apps_refreshed={s.get('apps_refreshed', 0)} "
            f"enriched={len(s['enriched'])}/{len(s['enrich_candidates'])}")
    mr = s.get("media_repair")
    if mr and mr.get("downloaded"):
        line += f" media_repair={mr.get('downloaded', 0)}"
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
    # Close any run rows orphaned by the previous process (SIGTERM on sleep, crash).
    # No run survives a restart, so every still-open row is stale — reaping them keeps
    # the *_ran_today gates and run-table diagnostics honest.
    if not dry_run:
        with begin(engine) as conn:
            reaped = reap_open_runs(conn)
        if reaped:
            print(f"[worker] reaped orphaned runs: {reaped}", flush=True)
    # Continuous discovery is the default: every loop tick runs a fresh keyword sweep
    # (no once-per-day gate), so the brand DB keeps growing as long as this machine is
    # awake. Set AGF_CONTINUOUS_DISCOVERY=0 to fall back to the daily-gated cadence.
    continuous = _env_bool("AGF_CONTINUOUS_DISCOVERY", True)
    cfg = dict(
        sweep_pages=_env_int("AGF_SWEEP_PAGES", 2),
        kw_delay=_env_float("AGF_KW_DELAY", 3.0),
        enrich_budget=_env_int("AGF_DISCOVERY_ENRICH_BUDGET", 600),
        enrich_top_n=_env_int("AGF_ENRICH_TOP_N", 5),
        enrich_media_cap=_env_int("AGF_MEDIA_CAP", 120),
        enrich_ttl_hours=_env_int("AGF_ENRICH_TTL_HOURS", 168),
        score_after=_env_bool("AGF_SCORE_AFTER_SWEEP"),
        min_score=_env_float("AGF_MIN_GEM_SCORE", ALERT_THRESHOLD),
        continuous_discovery=continuous,
        funnel_daily=_env_bool("AGF_FUNNEL_DAILY", True),
        funnel_pages=_env_int("AGF_FUNNEL_MAX_PAGES", 3),
        funnel_confirm_limit=_env_int("AGF_FUNNEL_CONFIRM_LIMIT", 50),
        flywheel_daily=_env_bool("AGF_FLYWHEEL_DAILY", True),
        flywheel_pages=_env_int("AGF_FLYWHEEL_MAX_PAGES", 2),
        flywheel_max_trials=_env_int("AGF_FLYWHEEL_MAX_TRIALS", 40),
        flywheel_min_advertisers=_env_int("AGF_MINE_MIN_ADVERTISERS", 3),
        mine_min_score=_env_float("AGF_MINE_MIN_SCORE", 40.0),
        media_repair_per_cycle=_env_int("AGF_MEDIA_REPAIR_PER_CYCLE", 5),
    )
    # In continuous mode a full taxonomy sweep already takes many minutes (kw_delay ×
    # 305 keywords), so the inter-sweep sleep is just a short breather, not an hour.
    interval = _env_int("AGF_SWEEP_INTERVAL_SEC", 60 if continuous else 3600)
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
