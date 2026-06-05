"""The live worker entrypoint: `python -m agf.workers.main`.

Composes the already-tested pieces behind ONE guarded loop —
    build extractor (warms lazily via SessionPool) →
    run_discovery (Firehose B + EU→US bridge + 14-day confirm) →
    [optional] run_scoring →
    enrich flagged gems via run_worker(make_enrich_handler(deps_factory))
— then sleeps and repeats. Previously the ops `worker` service ran a print-and
-exit stub; this is the real loop it should run.

SAFE BY DEFAULT for dry-runs. With AGF_DRY_RUN=1 it swaps an in-process
FakeExtractor (zero Meta traffic), runs exactly ONE sweep, and enriches the
just-confirmed advertisers so the whole path executes offline. Every sweep is
bounded by env caps so a real run can't hammer Meta.

Env:
  AGF_DATABASE_URL        Postgres DSN (required)
  AGF_PROXIES             residential proxy pool for the real Extractor
  AGF_DRY_RUN=1           fake extractor + run once, no network  (also: --dry-run / --once)
  AGF_EU_COUNTRIES        comma list for Firehose B            (default DE,GB)
  AGF_EU_MAX_PAGES        pages per EU country sweep            (default 3)
  AGF_MIN_RUNNING_DAYS    EU collapse floor                    (default 25)
  AGF_MIN_LONGEVITY_DAYS  scaling-confirm floor                (default 14)
  AGF_ENRICH_LIMIT        max gems enriched per sweep          (default 10)
  AGF_ENRICH_TOP_N        ads enriched per gem                 (default 5)
  AGF_ENRICH_TTL_HOURS    re-enrich a gem at most this often   (default 168 = 7d)
  AGF_SCORE_AFTER_SWEEP=1 run run_scoring after discovery      (default off; cron owns it)
  AGF_MIN_GEM_SCORE       enrich gems at/above this score      (default 70)
  AGF_SWEEP_INTERVAL_SEC  sleep between sweeps                  (default 3600; ignored if once)
"""
import asyncio
import os
import sys
import time
from datetime import date, datetime, timezone, timedelta

from sqlalchemy import text

from agf.db.engine import get_engine, begin
from agf.discovery.run_discovery import run_discovery
from agf.enrich_full.run_enrich import make_enrich_handler, EnrichDeps
from agf.scoring.run_scoring import run_scoring, ALERT_THRESHOLD
from agf.workers.runner import run_worker


# --- env helpers -------------------------------------------------------------
def _env_int(name, default):
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_bool(name):
    return os.environ.get(name, "").lower() in ("1", "true", "yes", "on")


def _env_list(name, default):
    raw = os.environ.get(name)
    return tuple(x.strip() for x in raw.split(",") if x.strip()) if raw else default


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


def production_deps_factory(extractor):
    """Full per-gem deps (RUNBOOK §5): a FRESH extractor.search for current creative
    URLs (fbcdn signed URLs expire) + real iTunes/HTTP/S3/WHOIS clients. Clients are
    lazy-imported so a dry-run never needs httpx/boto3/whois installed."""
    def factory(conn, advertiser_id):
        import httpx
        from agf.storage.s3 import get_s3, put_bytes
        try:
            import whois as _whois
            whois_fn = _whois.whois
        except Exception:
            whois_fn = None

        client = httpx.Client(timeout=20, follow_redirects=True)

        def fetch_text(url):
            return client.get(url).text

        def fetch_bytes(url):
            r = client.get(url)
            return r.content, r.headers.get("content-type", "")

        # fresh creative URLs via a live page search (signed URLs expire fast)
        page_id = conn.execute(
            text("SELECT page_id FROM public.advertisers WHERE id=:a"),
            {"a": advertiser_id}).scalar()
        creative_urls = {}
        if page_id:
            ads = asyncio.run(extractor.search(query="", countries=["US"],
                                               search_type="page", view_all_page_id=page_id))
            for ad in ads:
                urls = [("image", u) for u in (getattr(ad, "image_urls", None) or [])]
                urls += [("video", u) for u in (getattr(ad, "video_urls", None) or [])]
                if urls:
                    creative_urls[str(ad.ad_archive_id)] = urls

        return EnrichDeps(extractor=extractor, itunes_client=client,
                          fetch_text=fetch_text, fetch_bytes=fetch_bytes,
                          put=put_bytes, s3=get_s3(), whois_fn=whois_fn, page=None,
                          proxy=os.environ.get("AGF_FUNNEL_PROXY"),
                          capture=True, creative_urls=creative_urls)
    return factory


# --- queries -----------------------------------------------------------------
def _advertiser_ids_for_pages(conn, page_ids):
    if not page_ids:
        return []
    return [r[0] for r in conn.execute(text(
        "SELECT id FROM public.advertisers WHERE page_id = ANY(:p) ORDER BY id"),
        {"p": list(page_ids)}).all()]


def eligible_gem_ids(conn, min_score):
    """Flagged gems (alert + score≥min) — the human-review/enrich set (§3.7)."""
    return [r[0] for r in conn.execute(text(
        "SELECT advertiser_id FROM frontend.emerging_scaling_apps "
        "WHERE alert = TRUE AND gem_score >= :s ORDER BY gem_score DESC"),
        {"s": min_score}).all()]


# --- one sweep ---------------------------------------------------------------
def run_once(engine, extractor, *, now_epoch, as_of, dry_run, deps_factory,
             eu_countries=("DE", "GB"), eu_max_pages=3, min_running_days=25,
             min_longevity_days=14, enrich_limit=10, enrich_top_n=5,
             enrich_ttl_hours=168, score_after=False, min_score=ALERT_THRESHOLD) -> dict:
    """One discovery→(score)→enrich pass. Returns a summary dict (no printing)."""
    # 1) discovery — one committed transaction
    with begin(engine) as conn:
        disc = asyncio.run(run_discovery(
            conn, extractor, now_epoch, eu_countries=tuple(eu_countries),
            eu_max_pages=eu_max_pages, min_running_days=min_running_days,
            min_longevity_days=min_longevity_days))

    # 2) scoring — optional; its own committed txn + CONCURRENT matview refresh
    scored = run_scoring(engine, as_of) if score_after else None

    # 3) enrich — one transaction per gem (run_worker, source=engine). A dry-run has
    #    no scored history yet, so enrich the just-confirmed advertisers to drive the
    #    path; production enriches flagged gems (alert + score≥min).
    with engine.connect() as conn:
        cand_ids = (_advertiser_ids_for_pages(conn, disc["confirmed"]) if dry_run
                    else eligible_gem_ids(conn, min_score))
    due_before = datetime.now(timezone.utc) - timedelta(hours=enrich_ttl_hours)
    enriched = run_worker("enrich", "advertiser",
                          make_enrich_handler(deps_factory, top_n=enrich_top_n),
                          source=engine, candidate_ids=cand_ids,
                          due_before=due_before, limit=enrich_limit)
    return {"discovery": disc, "scored": scored,
            "enrich_candidates": cand_ids, "enriched": enriched}


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
    d = s["discovery"]
    line = (f"[worker] sweep: candidates={d['candidates']} pages={d['pages']} "
            f"confirmed={len(d['confirmed'])} "
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
        eu_countries=_env_list("AGF_EU_COUNTRIES", ("DE", "GB")),
        eu_max_pages=_env_int("AGF_EU_MAX_PAGES", 3),
        min_running_days=_env_int("AGF_MIN_RUNNING_DAYS", 25),
        min_longevity_days=_env_int("AGF_MIN_LONGEVITY_DAYS", 14),
        enrich_limit=_env_int("AGF_ENRICH_LIMIT", 10),
        enrich_top_n=_env_int("AGF_ENRICH_TOP_N", 5),
        enrich_ttl_hours=_env_int("AGF_ENRICH_TTL_HOURS", 168),
        score_after=_env_bool("AGF_SCORE_AFTER_SWEEP"),
        min_score=float(_env_int("AGF_MIN_GEM_SCORE", int(ALERT_THRESHOLD))),
    )
    interval = _env_int("AGF_SWEEP_INTERVAL_SEC", 3600)
    mode = "DRY-RUN (fake extractor, no Meta traffic)" if dry_run else "LIVE"
    print(f"[worker] starting — mode={mode} once={once} caps={cfg}", flush=True)
    _touch_heartbeat()                                # healthy before the first (slow) sweep

    while True:
        now_epoch = int(time.time())
        extractor = build_extractor(dry_run, now_epoch)
        deps_factory = (dry_run_deps_factory(extractor) if dry_run
                        else production_deps_factory(extractor))
        try:
            summary = run_once(engine, extractor, now_epoch=now_epoch,
                               as_of=date.today(), dry_run=dry_run,
                               deps_factory=deps_factory, **cfg)
            print(_summary_line(summary), flush=True)
        except Exception as exc:                      # one bad sweep must not kill the loop
            print(f"[worker] sweep failed: {exc!r}", file=sys.stderr, flush=True)
            if once:
                raise
        _touch_heartbeat()
        if once:
            break
        time.sleep(interval)


if __name__ == "__main__":
    main()
