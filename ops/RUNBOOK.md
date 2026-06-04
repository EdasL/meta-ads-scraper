# Field Desk — Operations RUNBOOK

Production stack: `ops/docker-compose.yml` (7 services: postgres, minio, minio-init,
api, worker, cron, proxy). Credentials live in `ops/.env` (template: `ops/.env.example`).
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

## 3. Proxy refresh

Proxies are the single env contract **`AGF_PROXIES`** (newline/comma list of
`user:pass@host:port`), read by Plan 2 `ProxyPool.from_env` and the `proxy` gateway
(`ops/proxy/gateway.conf`). To rotate: update `AGF_PROXIES` in `ops/.env` and
`docker compose -f ops/docker-compose.yml up -d worker proxy`. Use rotating residential
or static ISP IPs; avoid datacenter ASNs at volume.

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

## 5. Enrichment worker wiring (Plan 6)

Enrichment runs for **top-N flagged gems only** (`frontend.emerging_scaling_apps WHERE
alert`). Wire it with the §3.9 adapter: `agf.enrich_full.run_enrich.make_enrich_handler(
deps_factory)`, where `deps_factory(conn, advertiser_id)` builds `EnrichDeps` — crucially
doing a **fresh `extractor.search(view_all_page_id=page_id)`** to fetch current creative
URLs, because **fbcdn signed URLs expire** (download at scrape time). The Node
`google-play-scraper` sidecar (`src/agf/enrich_full/sidecar`, needs `npm install`) and
Playwright (optional/standby, funnel screenshots) are bundled in the app image.

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
