# Corpus refresh → pipeline_jobs queue (2026-07-13)

## Why now

2026-07-13 incident: the first 9000-budget corpus-refresh run (run 11) took 7h55m
INSIDE the sequential worker tick (02:55–10:50 UTC), freezing discovery the whole
time (6 candidates that morning vs 842 the day before). Root cause is design, not a
bug: the corpus now contains the keeper-absorbed advertisers (~120 ads each), so
per-advertiser refresh cost roughly doubled (run 11: 58,477 ads stored, 386/750
truncated at the 12-page cap vs ~65/250 on prior runs; errors=0). At 9000/day the
tick loses ~8h/day to this stage, every day. Founder approved moving it off the
tick (chat, 2026-07-13).

## Design

Reuse the keeper absorption machinery end to end. No new service, no new Meta
streams — the existing 3 keeper lanes drain a second job kind when idle.

**Producer (worker tick, replaces the blocking stage):**
- `corpus_due_for_refresh(conn, *, stale_hours, err_ttl_hours, limit)` in
  `discovery/corpus_refresh.py`: advertisers with a page_id and ≥1 stored ad whose
  freshest ad `last_seen` is older than `stale_hours`, excluding advertisers with a
  `logging.crawl_log` row `entity_type='corpus_refresh'` fresher than
  `err_ttl_hours` (failure suppression — same starvation-hot-loop defense as
  keepers). Stalest first (`MAX(ad.last_seen) ASC`), `LIMIT limit`.
- Tick enqueues `enqueue_jobs(conn, "corpus_refresh", ids)` every tick,
  `AGF_CORPUS_JOBS_PER_TICK` (default 16 ≈ 768/day at 1800s ticks — preserves the
  founder-approved ~750/day pace). Active-unique index dedups re-enqueue.
- The inline blocking stage flips default-off behind `AGF_CORPUS_REFRESH_INLINE`
  (default 0), mirroring `AGF_KEEPER_HISTORY_INLINE`. `run_stage corpus` and
  `scripts/refresh_corpus_ads.py` (operator one-shots) stay untouched;
  `discovery.corpus_refresh_runs` keeps recording only those manual runs.
- Tick summary line gains `corpus_enqueued=N`.

**Consumer (keeper daemon, `discovery/keeper_daemon.py`):**
- `process_once` claims by kind priority: `keeper_history` first, `corpus_refresh`
  only when no keeper job is claimable. Found→visible keeps absolute priority;
  total concurrent Meta streams unchanged (3 lanes shared).
- Subprocess runner passes `--kind corpus_refresh --flip-vanished` for corpus jobs.
- Safety net (subprocess dies without recording) marks crawl_log with the JOB's
  kind, not the hardcoded keeper kind.

**One-shot CLI (`discovery/keeper_one.py`, generalized not duplicated):**
- Gains `--kind` (default `keeper_history`) and `--flip-vanished` (default off).
  `set_status` + `mark_crawled` use the kind; `refresh_one_advertiser` gets
  `flip_vanished`. One shared failure path — both kinds inherit every fix.

**Brake (minimal, automated):**
- Before claiming a corpus job, the daemon checks corpus error jobs finished in the
  last 30 min; at ≥ `AGF_CORPUS_ERROR_PAUSE` (default 10) it skips corpus claims
  (keepers unaffected). Manual dial order for Meta-stream pressure stays:
  keeper lanes → corpus cadence → sweep.

**Migration 154:** `ix_ads_advertiser_last_seen ON public.ads(advertiser_id,
last_seen)` — the due query now runs ~48x/day instead of daily. (No bare percent
anywhere in the file, including comments.)

## Failure paths (enumerated per the 2026-07-10 lesson)

- **Success on a DEAD advertiser** (live-verify finding, added post-ship): an empty
  listing bumps no `ads.last_seen`, so success itself must stamp progress —
  keeper_one writes crawl_log `'ok'` on corpus success, and the due query
  suppresses ANY corpus marker fresher than `stale_hours`. Without this the
  stalest-first queue re-enqueued the same dead advertisers every tick.
- Pull raises → keeper_one writes error status + crawl_log 'corpus_refresh' marker
  → producer TTL-suppresses for `err_ttl_hours` (168h default).
- Subprocess killed by outer timeout → daemon safety net writes error + kind-aware
  crawl_log marker.
- Daemon crash mid-job → lease expiry → `reap_expired` requeues (or errors at
  max_attempts).
- Terminal-row purge: unchanged — `purge_terminal` is kind-blind and already runs
  from the enrich pipeline daemon (RUNBOOK §Pipeline-jobs purge coupling).

## Knobs

- `AGF_CORPUS_JOBS_PER_TICK=16` (pace; ~daily-target × tick ÷ 86400)
- `AGF_CORPUS_STALE_HOURS=48` (floor — never re-enqueue fresher than this)
- `AGF_CORPUS_ERR_TTL_HOURS=168` (failure suppression, mirrors keeper TTL)
- `AGF_CORPUS_ERROR_PAUSE=10` (errors/30min that pause corpus claims)
- `AGF_CORPUS_REFRESH_INLINE=0` (legacy in-tick stage, off)
- `AGF_CORPUS_REFRESH_BUDGET` becomes inert for the tick (still honored by
  `run_stage corpus --budget` and the one-shot script).

## Non-goals

- No new daemon/compose service; no keeper-lane count change (Meta stream budget).
- No automated keeper/sweep throttling (manual dial order stands).
- No change to scoring/compute cadence — the */15 compute cron already consumes
  refreshed `is_active`/metrics.
