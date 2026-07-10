# Worker architecture refactor — from sequential loop to concurrent queue-driven pipeline

Date: 2026-07-10
Status: research/design (nothing built)

## What actually runs today (verified against code, not the docs)

- One worker container runs `agf.workers.main` — a `while True` loop
  (`main.py:1080`) executing ~17 stages strictly sequentially
  (`run_once`, `main.py:500-891`): sweep → triage → velocity → metadata →
  store refresh → keeper history → funnel → corpus refresh → frame-mint →
  flywheel → scoring → embeddings/crowding → preference → candidate
  embeds → enrich → media repair → funnel repair → websub.
- **Meta GraphQL concurrency is 1.** `run_sweep` iterates keywords in a
  plain `for` loop, one `search_page` await at a time; `SessionPool`
  holds a single warm session; pacing is sleep-based (`AGF_KW_DELAY` 3s,
  `AGF_SWEEP_PAGE_DELAY` 2s). There is no ThreadPoolExecutor, no
  `asyncio.gather`, no semaphore anywhere in discovery/enrich.
- A full cycle takes ~40 min (comment at `main.py:909`), almost all of
  it serial network waits + sleeps. Every stage blocks every other
  stage: scoring waits for sweeps, enrich waits for scoring, etc.
- **There is no "night" gating.** Stages are gated per-day/interval via
  `*_runs` tables (`discovery_due`, `funnel_due`, `flywheel_due`, …).
  Prod sets `AGF_CONTINUOUS_DISCOVERY=0`, so the sweep runs once per
  day — as one big burst whenever the gate opens, then the loop mostly
  idles between gates. The daily *cadence per keyword* is deliberate
  (re-sweep collapse, see `docs/research/2026-07-02-sustained-discovery-algorithm.md`);
  the *burst shape* and the serialization are the accidents.
- One real job queue already exists: `public.enrich_jobs` +
  `enrich_full/queue_daemon.py` — `FOR UPDATE SKIP LOCKED` claim, one
  job per poll, subprocess isolation. It serves on-demand "Refresh
  data" only, but it is the proven seam this design generalizes.

So the founder-visible symptoms are real, with one correction: the
problem is not "scraping only at night", it's "one thread does
everything in a fixed order, with a once-a-day burst per lane".

## External research verdict (practitioner consensus, 2024-2026)

- **Postgres-as-queue via SKIP LOCKED is the right call at this scale.**
  The adversarial sources put the breaking point at thousands of
  concurrent workers / thousands of jobs per second — three orders of
  magnitude above us (single-digit workers, <100k jobs/day). Known
  failure modes to pre-empt even at our scale: queue-table bloat
  (insert→update→delete churn thrashes the partial index — tune
  autovacuum on the table, keep payloads thin, archive done rows) and
  long-held claiming transactions (claim fast, work outside the txn —
  already a house rule).
- **Do not add Redis/Celery/Kafka.** Strong "boring Postgres" consensus
  when Postgres is already present and throughput isn't the bottleneck.
  Ours never will be: the ceiling is residential-proxy rate limits, not
  job dispatch.
- **Per-item recurrence = `next_run_at` column + due-time polling with
  SKIP LOCKED.** This is the standard pattern; it's how Procrastinate /
  PGQueuer / pg_cron model periodics internally.
- **Rate-limited scrape concurrency = semaphore per proxy + token
  bucket + backoff on 429/challenge.** curl_cffi has an
  `AsyncSession`, but it has open proxy-isolation bugs (lexiforest/curl_cffi
  #41: sessions can reuse the first request's proxy; #687: 0.14.0 SOCKS5
  async timeout regression). Threads + the existing sync sessions are
  the safe first move; verify AsyncSession proxy isolation with one
  live probe before ever switching to it.
- **Skip orchestrators** (Temporal, Prefect, Dagster, Hatchet) at
  solo-founder scale. If durable multi-step workflows ever hurt, DBOS
  is the lowest-switching-cost option (state lives in our Postgres),
  but "each stage = a queue consumer" is the right decoupling now.
- Library shortlist if we don't hand-roll: **Procrastinate** (async,
  retries, cron, per-resource locks, LISTEN/NOTIFY) or **PGQueuer**
  (minimal). Recommendation below is to extend our own proven
  `enrich_jobs` pattern instead — fewer deps, and we already run it.

## Target architecture

Postgres stays the only shared medium. Three consumer processes
replace the one mega-loop, all in the same compose file on the same VM:

1. **`scraper`** — the only process that talks to Meta. Pulls
   fetch-shaped jobs (sweep a keyword, deep-page a keyword, enrich an
   advertiser's ads, confirm a funnel, refresh corpus ads) from the
   jobs table. Runs N concurrent fetchers where N = number of proxies
   in rotation (start at 3-4 — the level live-proven on DataImpulse in
   June; do not jump higher without measuring challenge rates). Each
   fetcher holds a per-proxy semaphore of 1 and its own warm session;
   a shared token bucket enforces the global request pace; consecutive
   proxy errors still trip the existing circuit breaker.
2. **`compute`** — triage, scoring, embeddings, crowding refresh,
   preference scoring, media repair. No Meta traffic, so it runs fully
   in parallel with scraping. CPU/Gemini-bound; single process,
   sequential inside is fine.
3. **`enrich`** — the existing queue daemon, unchanged semantics,
   folded onto the same jobs table (its `enrich_jobs` shape *is* the
   template).

### The jobs table

Generalize `enrich_jobs` into one `pipeline_jobs` table:

- `id, kind, payload jsonb (thin — ids only), status, run_after,
  attempts, max_attempts, claimed_at, lease_expires_at, last_error`.
- Claim: `UPDATE ... SET status='running', lease_expires_at=now()+lease
  WHERE id = (SELECT id FROM pipeline_jobs WHERE status='queued' AND
  run_after <= now() ORDER BY priority, run_after LIMIT 1 FOR UPDATE
  SKIP LOCKED) RETURNING ...`. Claim in its own short transaction; the
  work runs outside any transaction (existing house rule).
- A crashed worker's jobs recover via lease expiry: a reaper flips
  `running` rows with expired leases back to `queued` and bumps
  `attempts`. This replaces the PID-mutex + self-SIGKILL-only recovery.
- Done rows get deleted (or moved to an archive table) after N days;
  set aggressive autovacuum storage parameters on the table at
  creation. Both are the bloat pre-emption the research flagged.

### Messaging: "tell the next worker when a step is done"

Chained enqueues, not a bus. Finishing a job enqueues its successor in
the same DB write that records the result:

- sweep page lands candidates → enqueue `triage` for the new batch;
- triage keeps an advertiser → enqueue `embed_candidate` and, if it
  clears the gate, `enrich`;
- enrich completes → enqueue `score(advertiser_id)` and `media_repair`
  as needed;
- scoring completes → nothing (matviews/feed read state as today).

Consumers poll every ~5s (the enrich daemon already does); at our job
rates that is indistinguishable from instant. `LISTEN/NOTIFY` wakeups
are a later nice-to-have, not part of the initial build.

### Continuous scheduling: kill the daily burst, keep the daily cadence

Replace the `*_runs`-table daily gates with per-item recurrence on the
domain rows themselves:

- `dynamic_keywords.next_sweep_at` — when a keyword's sweep job
  finishes, reschedule it `now() + interval` where interval comes from
  the keyword's lane/yield (daily for active, longer for resting —
  the existing lifecycle already encodes this). A tiny scheduler tick
  (or the scraper itself) enqueues due keywords continuously. Net
  effect: the same one-sweep-per-keyword-per-day budget, spread evenly
  across 24h instead of a burst — which is also gentler on the proxies
  and means fresh candidates flow into triage/review all day.
- Same pattern for app-store refresh, keeper history, funnel confirm,
  corpus refresh (its budget becomes a token allocation per day rather
  than a per-cycle count).
- Weekly lanes (funnel deep sweep, generic deep sweep) become jobs with
  `run_after = last + 7d` instead of `*_ran_within_days` checks.
- Keep the `*_runs` tables as run *logs*; they just stop being gates.

**Explicitly preserved:** the once-per-day-per-keyword cadence. The
07-02 research showed re-sweeping the same keywords more often
collapses yield; "continuous" here means continuous *flow*, not more
re-sweeps.

### Intra-sweep concurrency

Inside the scraper, a keyword sweep is still sequential per keyword
(pages of one SERP are inherently serial), but K keywords run
concurrently across K proxies. First implementation:
`ThreadPoolExecutor(K)` over the existing sync curl_cffi sessions —
zero new failure modes, the sync path is battle-tested. Move to
curl_cffi `AsyncSession` only after a live probe proves per-request
proxy isolation (bug #41) on our pinned version.

### Guardrails carried over

- `init: true`, restart policy, per-container heartbeat + watchdog
  (heartbeat now per consumer process; stale = self-kill, compose
  restarts).
- Circuit breaker on consecutive proxy errors aborts the scrape lane,
  not the whole pipeline.
- Subprocess isolation stays for the crash-prone enrich path.
- No network/LLM calls inside DB transactions.
- Budgets survive as daily token allocations enforced by the scraper's
  token bucket instead of per-stage counters.

## What this buys

- Wall-clock: today's ~40 min serial cycle is mostly waits; 3-4-way
  scrape concurrency plus a parallel compute lane cuts sweep wall time
  ~3-4x at the same request budget — or supports 3-4x more keywords at
  the same wall time (which is the actual goal: more coverage).
- Latency: candidates hit triage/review minutes after discovery all
  day, instead of once per day after the burst.
- Resilience: a wedged enrich no longer stalls sweeping; a crashed
  process's jobs are reclaimed by lease expiry instead of waiting for
  the watchdog + full-cycle restart.
- On-demand actions ("Refresh data") and the pipeline share one queue
  mechanism instead of two.

## Migration path — five shippable steps, each independently valuable

1. **Concurrent sweep inside the current loop** (no schema change).
   Fan `run_sweep` out over a `ThreadPoolExecutor(K=3)` with one proxy
   + session per thread, keep per-thread pacing delays, keep the
   circuit breaker. Biggest single win, smallest diff. Live-verify
   challenge/error rates at K=3 before considering K=4+.
2. **Split the container in two**: `scrape-lane` (stages 1, 1.9, 1.91,
   3.5/3.6 network parts) and `compute-lane` (triage, scoring,
   embeddings, preference, crowding), each its own loop + heartbeat.
   Pure compose + entrypoint split; the lanes already hand off through
   tables.
3. **`pipeline_jobs` table + migration of enrich** onto it (the daemon
   already has the right shape); add lease expiry + reaper + archival.
4. **Chained enqueues** for triage → embed → enrich → score; delete the
   corresponding stage blocks from the loop.
5. **Per-item `next_*_at` scheduling** for keywords, funnels, corpus
   and app refresh; retire the daily-gate checks; the "loop" shrinks to
   consumers + a scheduler tick.

Each step ships alone, is verifiable live (challenge rate, cycle time,
queue depth), and steps 3-5 can stop at any point with the system
strictly better than today.

## Off-the-shelf frameworks evaluated (and why not)

Direct-fit tools exist for both halves of the problem; verdict per tool:

- **Crawlee for Python** (Apify, v1.8, active) — the only real contender.
  It ships exactly the four things we want: `CurlImpersonateHttpClient`
  (curl_cffi is first-class, TLS impersonation transfers), SessionPool
  with session-proxy binding + retirement, tiered ProxyConfiguration,
  AutoscaledPool concurrency, and an `HttpCrawler` mode for pure
  API/JSON work. Rejected because (a) its Postgres-backed RequestQueue
  (`SqlStorageClient`) is explicitly flagged experimental — we'd be
  trusting an unstable component as the pipeline medium, exactly where
  we're strong today; (b) we'd adopt its RequestQueue/router
  programming model wholesale around a fetch layer it can't replace
  (session warming, doc_ids, rd_challenge stay ours regardless).
  Worth mining its SessionPool design when building our session
  manager. Revisit only if we ever need generic HTML crawl breadth.
- **Scrapy** (+ scrapy-impersonate, maintained, v1.7.0 2026-05) —
  rejected. Twisted-based, request-centric, stateless-page-oriented;
  sticky-session-per-proxy warmed GraphQL POSTs fight its middleware
  model. Community consensus: overkill for API scrapers of this shape.
- **Procrastinate / PGQueuer** — direct fit for the queue/scheduling
  half on our existing Postgres (SKIP LOCKED underneath, retries,
  periodic tasks, Procrastinate adds per-resource locks +
  LISTEN/NOTIFY). Legitimate alternative to extending `enrich_jobs` by
  hand; choose Procrastinate if we'd rather not own retry/lease/cron
  plumbing, hand-roll if we want zero new deps. Either way the schema
  medium stays our Postgres.
- **Botasaurus / Scrapling / hrequests** — browser-automation,
  HTML-parsing, and low-level TLS niches respectively; wrong problem.

Practitioner pattern across reports: frameworks pay off when the crawl
graph (link discovery, HTML extraction, dedup) is the work, and get
fought when the hard part is a bespoke protocol. Ours is the latter.

## What we deliberately do not build

- No Redis, Celery, Kafka, or message broker — Postgres SKIP LOCKED
  covers our scale by ~2-3 orders of magnitude.
- No workflow orchestrator (Temporal/Prefect/Dagster/Hatchet); revisit
  DBOS only if multi-step durability ever actually hurts.
- No per-page fan-out of a single keyword's SERP (pages are serial by
  design), no higher re-sweep frequency (yield collapse), no >4 proxy
  concurrency until challenge rates at 3-4 are measured live.

## Sources

- richyen.com "Potential Consequences of Using Postgres as a Job
  Queue" (2026-05) — failure modes + explicit "at small scale it's the
  right call".
- HN 42036303 + atomicobject "Do you need Redis?" — Postgres-first
  consensus.
- stevenyue.com task-queue load test (2025-11) — Procrastinate ~740
  jobs/s with 10 workers; dispatch throughput is not our constraint.
- Procrastinate / PGQueuer docs — periodic tasks as next-run polling.
- lexiforest/curl_cffi issues #41, #687 — async proxy-isolation and
  SOCKS5 regressions; why threads first.
- docs/research/2026-07-02-sustained-discovery-algorithm.md — why the
  per-keyword daily cadence must survive the refactor.
