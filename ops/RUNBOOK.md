# Field Desk — Operations RUNBOOK

Production stack: `ops/docker-compose.yml` (7 services: postgres, minio, minio-init,
api, api-py, worker, cron — proxies are in-process via `AGF_PROXIES`, not a gateway
container). Credentials live in `ops/.env` (template: `ops/.env.example`).
Bring it up: `docker compose -f ops/docker-compose.yml --env-file ops/.env up -d`.

The Ad Library scrape is a **maintenance treadmill** (master-plan §2): the `doc_id`
and session tokens rotate, and proxies degrade. This runbook is how you keep it alive.

---

## 1. doc_id / token rotation (the most common failure)

**Symptom.** `GET /api/health/metrics` returns a `doc_id_rotation` alert (or HTTP 400 /
empty edges on a known-good query). The scraper started 403/400-ing.

**Why.** Facebook rotates the Relay persisted-query `doc_id` (~2–4 weeks) and the
`rd_challenge` cookie expires (24h). `crawl_log.crawl_result` fills with `doc_id` / `400`
errors → `monitoring/metrics.rotation_alerts` fires.

**Fix (re-heal, no browser needed).**
1. The Plan 2 transport already self-heals each warm cycle: `agf.extract.rd_challenge`
   solves the `rd_challenge` (regex the `/__rd_verify_…` stub → empty POST → cookie),
   and `agf.extract.token_heal` re-harvests `LSD`/`fb_dtsg`/`doc_id` from the page HTML +
   JS bundle. Restart the `worker` (and the Plan 2 warmer) to force a fresh heal.
2. If `doc_id` can't be harvested from the bundle, the known-good fallbacks are
   `27201872659451053` (live-verified 2026-06-04), `25464068859919530`, `33730956806489539`.
3. If `rd_challenge` ever becomes a *computed* challenge, enable the standby browser
   warmer (`agf.extract.warmer.browser_warm`, needs `playwright` + `playwright install chromium`).

**Verify.** Run a US search end-to-end and confirm ≥1 ad parses; the
`doc_id_rotation` alert on `/api/health/metrics` clears.

---

## 2. Rate limiting

**Symptom.** `rate_limit` alert on `/api/health/metrics`; `crawl_result` shows `429` /
`1675004`. **Fix.** The Plan 2 `with_backoff` already backs off; reduce worker concurrency
and rotate proxies (see §3). Pull creative bytes outside the residential proxy.

---

## 3. Proxies (`AGF_PROXIES`)

Proxies are **in-process** — the single env contract **`AGF_PROXIES`** (newline/comma list
of `user:pass@host:port`) is read by `ProxyPool.from_env` (`src/agf/extract/proxy.py`) and
applied per request via curl_cffi. There is **no proxy gateway container** (the old
placeholder was a no-op and was removed).

Requirements (Meta is strict):
- **Residential or static-ISP IPs, never datacenter** — Meta blocks datacenter ASNs at volume.
- **Sticky sessions** — the warm session holds an `rd_challenge` cookie (~24h); the IP must
  stay stable for the session's life or the cookie/TLS fingerprint mismatches and re-challenges.
  Most providers expose this via a session id in the username
  (e.g. `user-session-ab12:pass@gate.host:port`). Use one sticky endpoint per pool entry.
- **Several entries** — the pool round-robins and benches a proxy after 3 failures for 5 min
  (`max_failures=3`, `cooldown=300s`).
- Optional `AGF_FUNNEL_PROXY` for enrichment funnel-capture / creative downloads (use a cheaper
  proxy or leave unset = direct, to spare residential bandwidth).

To set/rotate: edit `AGF_PROXIES` in `ops/.env`, then
`docker compose -f ops/docker-compose.yml --env-file ops/.env up -d worker`.
**Verify before crawling:** `AGF_PROXIES="…" PYTHONPATH=src python scripts/check_proxies.py`
(shows each proxy's egress IP/country/ISP + a live Meta smoke through the pool).

---

## 4. Scheduled jobs (the `cron` service)

`ops/cron/crontab` installs (UTC):
- `snapshot.sh` (02:15) — daily re-score: `agf.scoring.run_scoring.run_scoring` (features →
  GemScore → vetoes → write `adtech.advertiser_scores` → refresh matview).
- `refresh_matview.sh` (hourly) — `REFRESH MATERIALIZED VIEW CONCURRENTLY
  frontend.emerging_scaling_apps` (needs Plan 5's UNIQUE index; own autocommit stmt).
- `prune.sh` (03:30) — drop `logging.crawl_log` + `adtech.ad_metrics` older than 365 days.
- `backup.sh` (04:00) — `pg_dump | gzip` to `$BACKUP_DIR`.

Logs: `/var/log/agf/*.log`.

---

## 5. The worker loop (`agf.workers.main`)

The `worker` service runs `python -m agf.workers.main` — one guarded loop that each
sweep: builds the Extractor (warms a curl_cffi session lazily via `SessionPool` +
`ProxyPool.from_env`) → `run_discovery` (Firehose B + EU→US bridge + 14-day confirm) →
optionally `run_scoring` → enriches flagged gems via `run_worker(make_enrich_handler(
deps_factory))`, then sleeps `AGF_SWEEP_INTERVAL_SEC` (default 1h) and repeats.

**Enrichment** runs for **flagged gems only** (`emerging_scaling_apps WHERE alert AND
gem_score ≥ AGF_MIN_GEM_SCORE`), capped at `AGF_ENRICH_LIMIT` per sweep, `AGF_ENRICH_TTL_HOURS`
between re-enriches. The production `deps_factory` does a **fresh
`extractor.search(view_all_page_id=page_id)`** for current creative URLs (**fbcdn signed
URLs expire**) and wires the iTunes/HTTP/S3/WHOIS clients; the Node `google-play-scraper`
sidecar (`src/agf/enrich_full/sidecar`, `npm install`) + standby Playwright are in the image.

**Sweep caps (so a real run can't hammer Meta), all with in-code defaults:**
`AGF_EU_COUNTRIES` (DE,GB) · `AGF_EU_MAX_PAGES` (3) · `AGF_MIN_RUNNING_DAYS` (25) ·
`AGF_MIN_LONGEVITY_DAYS` (14) · `AGF_ENRICH_LIMIT` (10) · `AGF_ENRICH_TOP_N` (5) ·
`AGF_ENRICH_TTL_HOURS` (168) · `AGF_SCORE_AFTER_SWEEP` (off — the cron owns daily scoring) ·
`AGF_SWEEP_INTERVAL_SEC` (3600). Set `AGF_PROXIES` before a live run.

**Dry-run (no Meta traffic).** `AGF_DRY_RUN=1` (or `--dry-run`) swaps an in-process
`agf.extract.fake.FakeExtractor`, runs exactly ONE sweep, and enriches the just-confirmed
advertisers — so the whole path can be smoked offline:
`docker compose -f ops/docker-compose.yml run --rm -e AGF_DRY_RUN=1 worker`, or locally
`AGF_DRY_RUN=1 AGF_DATABASE_URL=… PYTHONPATH=src python -m agf.workers.main`.

**Health.** The loop writes a heartbeat each sweep; the compose healthcheck runs
`python -m agf.workers.main --healthcheck` (fails if the heartbeat goes stale > 2 sweeps),
so a hung/crash-looping worker is reported down instead of silently restarting.

---

## 6. Restore from backup

To restore the database from a nightly `backup.sh` dump:

1. Stop the app: `docker compose -f ops/docker-compose.yml stop api worker cron`.
2. Restore: `gunzip -c $BACKUP_DIR/agf-<TS>.sql.gz | psql "$AGF_DATABASE_URL"`.
3. Re-apply migrations (idempotent): `PYTHONPATH=src python -c "from agf.db.engine import
   get_engine; from agf.db.migrate import apply_migrations; apply_migrations(get_engine())"`.
4. Rebuild the matview: `ops/cron/refresh_matview.sh`.
5. Restart: `docker compose -f ops/docker-compose.yml up -d`.

---

## 7. Health

- `GET /healthz` → liveness.
- `GET /api/health/metrics` → crawl rates, error counts, `alerts` (doc_id_rotation /
  rate_limit), and a `degraded` flag. Alert on `degraded == true`.
