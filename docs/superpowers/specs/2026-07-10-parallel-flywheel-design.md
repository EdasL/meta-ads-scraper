# Parallel flywheel + faster cadence + deep-retest lane — design

Date: 2026-07-10. Approved scope from founder conversation: speed up the whole
keyword-discovery mechanism (not just retesting), grow the trial pool, and
retest the shallow-judged retired keywords with genuinely deep paging.

## Why (measured, prod 2026-07-10)

- The dynamic-keyword flywheel sweeps **serially** (`workers/main.py` `run_flywheel_stage`,
  `for kw in live:`) at ~11 s/keyword; `AGF_SWEEP_WORKERS` only parallelizes the
  static taxonomy sweep. Worker sweeps ~4-5 h out of 24 — mostly idle.
- Live dynamic pool = 38 (23 active + 15 trial) of 1,899 total; trial ceiling
  `AGF_FLYWHEEL_MAX_TRIALS=40` is the inflow gate. Mints supply ~80/day.
- **955 of 1,177 retired keywords** were judged entirely pre-2026-07-08 (flat 2-4
  page sweeps), all killed at the 4-sweep trial limit with `new_candidates_total=0`
  and never deep-paged. Cohort gate: `status='retired' AND last_swept_at < '2026-07-08'`.
- Adaptive depth (patience=3) stops after 3 consecutive zero-new pages, so even a
  post-07-08 sweep of a barren-prefix keyword stops ~page 3 — a plain re-admission
  would repeat the shallow verdict. The retest's first sweep must force deep pages.
- Flywheel cadence: prod `AGF_FLYWHEEL_EVERY_SEC=7200`, worker tick 3600 →
  a trial verdict (4 sweeps, `AGF_FLYWHEEL_TRIAL_SWEEPS=4`) takes ~8h+.

## Changes

### 1. Parallel flywheel sweep (code)

New `run_parallel_flywheel` path inside `run_flywheel_stage` (src/agf/workers/main.py):

- Partition `live_keywords()` round-robin into K disjoint shards (reuse
  `_partition` from src/agf/discovery/sweep.py).
- `ThreadPoolExecutor(max_workers=K)`, one worker per (extractor, shard,
  worker_id) triple — same shape as `run_parallel_sweep` (sweep.py:333).
- Each thread runs the **unchanged per-keyword body**: single-entry `run_sweep`
  → on success `record_keyword_sweep` in its own short transaction. Per-keyword
  lifecycle writes touch only that keyword's row; shards are disjoint → no races.
  Candidate upserts inside `run_sweep` already commit in sorted order (the
  static-sweep deadlock fix) and are safe across concurrent workers.
- One shared `SweepBreaker(max_consecutive_errors, workers=K)` so a gateway-wide
  outage aborts the stage; per-worker streaks so one dead proxy slice can't
  false-abort (mirrors static sweep).
- Pass `heartbeat=_touch_heartbeat` per keyword (today the flywheel doesn't
  heartbeat; the parallel stage must, same as static, so the watchdog never
  kills a progressing pass).
- Stats merged across shards into the existing `flywheel_runs` shape
  (keywords_swept, candidates_new/seen, promoted, retired, errors). A
  `retested` count (§3) appears in the returned stats / worker log line only —
  no `flywheel_runs` schema change.
- Extractors: `build_extractors(dry_run, now_epoch, K)` (main.py:169), K from
  **new env `AGF_FLYWHEEL_WORKERS` (default 3)**. K=1 (and dry-run/tests) takes
  the existing serial path unchanged.
- Mining, `apply_verdicts`, trial admission, retest admission stay **serial,
  before the loop** — only the sweep loop parallelizes.

### 2. Faster cadence + proven-keyword spacing (env + one query change)

- Worker tick: `AGF_SWEEP_INTERVAL_SEC` 3600 → **1800** (prod env change).
- Flywheel every tick: `AGF_FLYWHEEL_EVERY_SEC` 7200 → **60** (gate at
  main.py:429 already supports this; 60 ≈ "every tick" at any sane tick length).
- Trial verdict latency becomes 4 sweeps × 30 min ≈ **2 h**.
- **Active-keyword spacing** so faster ticks don't re-hammer proven keywords:
  `live_keywords()` (src/agf/db/dynamic_keywords.py:137) additionally filters
  actives to `last_swept_at IS NULL OR last_swept_at <= now() - make_interval(
  hours => :gap)` with gap from **new env `AGF_FLYWHEEL_ACTIVE_GAP_HOURS`
  (default 12)**, threaded through as a parameter. Trials and woken resting
  keywords are always included (they need verdicts). Gap=0 disables (test hook).
- Trial ceiling: `AGF_FLYWHEEL_MAX_TRIALS` 40 → **200** (prod env change; code
  default stays 40).

### 3. Deep-retest lane (migration + code)

- **Migration 153** `db/schema/153_keyword_retest.sql`:
  `ALTER TABLE discovery.dynamic_keywords ADD COLUMN IF NOT EXISTS retested_at
  timestamptz` — stamps re-admission, prevents double-retest. (Migration rule:
  no bare percent character anywhere in the file, including comments.)
- New `admit_retest_keywords(conn, *, limit)` in src/agf/db/dynamic_keywords.py:
  re-admits up to `limit` rows matching
  `kind='app' AND status='retired' AND retested_at IS NULL AND
  last_swept_at < '2026-07-08'` (constant `RETEST_SHALLOW_CUTOFF = date of the
  adaptive-depth ship`), oldest `last_swept_at` first:
  `status='trial', sweeps_used=0, dry_streak=0, retested_at=now()`.
- Called from `run_flywheel_stage` after mint admission, filling only **room
  left over after mints** (mints keep priority), paced by **new env
  `AGF_FLYWHEEL_RETEST_PER_PASS` (default 0 = off; prod 50)**. Cohort (955)
  drains in ~2 days at prod settings, then the lane naturally no-ops forever.
- **Forced-deep first sweep**: `live_keywords()` also returns `retested_at`;
  in the sweep loop, a keyword with `retested_at IS NOT NULL AND sweeps_used=0`
  is swept with `patience_pages=max_depth` (patience disabled up to the depth
  cap, `AGF_SWEEP_MAX_DEPTH=15`) so a barren prefix can't cut it off before the
  deep pages the original verdict never saw. Subsequent sweeps of that keyword
  use normal adaptive settings; the normal 4-sweep trial verdict then applies.
- Retested keywords that fail again are retired with `retested_at` set →
  excluded from any future retest; ones that yield promote like any trial.

### 4. Explicitly NOT changing

- Static taxonomy sweep (daily, `AGF_SWEEP_WORKERS=3`) — untouched.
- Resting pool (684) — already self-wakes via `next_eligible_at` within ~7 days.
- The 222 retired keywords already judged under adaptive depth — stay retired.
- Enrich/triage/compute paths — already queue/cron-driven.
- Trial verdict policy (TRIAL_SWEEPS=4, dry-streak constants) — unchanged.
- Peak Meta concurrency — stays 3 connections (static sweep and flywheel run
  sequentially inside `run_once`; both use K=3).

## Error handling

- Throttled keyword (search error): unchanged — counts `errors`, no verdict,
  no trial budget burn; breaker records per-worker streaks.
- Breaker trip: stage aborts, partial stats recorded to `flywheel_runs`,
  remaining keywords wait for next tick (30 min away — cheap).
- Worker/thread death mid-pass: per-keyword transactions mean at most the
  in-flight keywords lose one sweep's credit; next pass re-sweeps them.

## Testing

- Unit (fakes): shard/merge parity with serial results; verdicts recorded per
  keyword under K=3; shared-breaker abort stops all shards; K=1 falls back to
  the exact serial path; stats shape matches `flywheel_runs` columns.
- DB tests (AGF_TEST_DB_URL): active-gap filter (active swept 1h ago excluded,
  13h ago included, trials always in); `admit_retest_keywords` respects cutoff,
  limit, room-after-mints, and never re-admits `retested_at IS NOT NULL`;
  forced-deep first sweep passes patience=max_depth (assert via fake extractor
  page count on a barren keyword); migration 153 applies to a fresh DB.
- Live gate (deployed container): one real flywheel pass with retest enabled at
  a small limit; verify `flywheel_runs` duration ≪ serial, errors=0, retested
  keywords show sweeps_used=1 and deep page counts; then enable prod envs.

## Rollout

- Envs (VM compose — edit BOTH repo `ops/prod/docker-compose.yml` and the
  hand-edited VM copy): `AGF_FLYWHEEL_WORKERS=3`, `AGF_SWEEP_INTERVAL_SEC=1800`,
  `AGF_FLYWHEEL_EVERY_SEC=60`, `AGF_FLYWHEEL_MAX_TRIALS=200`,
  `AGF_FLYWHEEL_ACTIVE_GAP_HOURS=12`, `AGF_FLYWHEEL_RETEST_PER_PASS=50`.
- Watch for 48 h: `flywheel_runs` (duration, errors, keywords_swept),
  Meta challenge rate (drop `AGF_FLYWHEEL_WORKERS` to 2 via env if it rises),
  DataImpulse traffic. Expect a ~2-day traffic bump while the retest drains.
