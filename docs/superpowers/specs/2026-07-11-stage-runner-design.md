# On-demand stage runner (`run_stage`) — design

Date: 2026-07-11. Founder ask: "on demand way to launch any parts so you could
test them every time something changes." Approved in conversation.

## Problem

Every pipeline stage runs only inside `run_once` behind daily/weekly/interval
gates. Verifying a change means waiting for a gate, or hand-piping one-off
scripts into the deployed container (~30 ad-hoc scripts have accreted this
way, each re-plumbing deps differently, none bypassing gates cleanly). Last
night's watchdog crash-loop took hours to surface for exactly this reason.

## Solution

One CLI, `python -m agf.workers.run_stage <stage> [options]`, that runs any
single stage **through the exact same functions and dep builders `run_once`
uses** — no parallel implementations — with the date gates bypassed.

### Stages (v1)

- `sweep` — `build_extractors` + `run_parallel_sweep` (+ `run_triage` after,
  same as the tick). Options: `--pages`, `--keywords a,b,c` (subset of
  taxonomy entries), `--workers`.
- `flywheel` — `run_flywheel_stage` with real extractors. Options:
  `--keywords a,b,c` (NEW `only_keywords` filter param on the stage — sweeps
  just those live keywords; mining/mint/retest admission skipped when set),
  `--max-trials`, `--mint-per-pass`, `--retest-per-pass`, `--active-gap-hours`,
  `--workers`.
- `funnel` — `run_funnel_stage`. Options: `--pages`, `--confirm-limit`,
  `--deep` (force the weekly deep pass).
- `corpus` — `refresh_corpus` (with the heartbeat hook). Options: `--budget`,
  `--limit`, `--offset`.
- `generic-deep` — `run_generic_deep_stage`. Options: `--pages`,
  `--mint-slots`.
- `mint` — `mint_keywords_from_releases` (frame/autocomplete/brainstorm
  lanes). Options: `--slots`, `--no-autocomplete`, `--no-brainstorm`.
- `triage` — `run_triage` with the real iTunes/Play lookups (drains
  status='new' candidates; no Meta traffic).
- `compute` / `score` — thin dispatch to the existing cron entry points
  (compute_cycle module / run_scoring), which already run standalone; the
  runner exists so ONE command surface covers everything.

### Semantics

- **Gates bypassed** — always runs, regardless of run-row history.
- **Test mode by default**: no `*_runs` row is written, so a manual run never
  satisfies a daily/weekly gate or pollutes run-history stats. `--record`
  opens/closes the real run row (for "run tonight's pass now" — e.g. today's
  corpus recovery).
- **Env-true defaults**: every knob defaults from the same `AGF_*` envs the
  worker reads, so inside the prod container a bare `run_stage flywheel` is
  a faithful production pass.
- **Output**: the stage's stats dict as JSON on stdout (single line); progress
  to stderr. Exit 0 on success, 1 on stage exception (traceback to stderr).
- **Heartbeat**: passes `_touch_heartbeat` where the tick does — a manual run
  inside the container also keeps the (shared) heartbeat fresh, never staler.
- **Concurrency note (doc, not code)**: the runner does not take the worker's
  pidfile mutex; a small scoped run alongside the live worker is fine (same
  proxy pool, DataImpulse rotates per connection). Full-size manual passes
  while a tick is sweeping double Meta traffic — the doc says so.

### Explicitly NOT in v1

- No enrich stage (already on-demand: `pipeline_jobs` enqueue or the
  dashboard's Refresh data / `enrich_one`).
- No scheduling/locking — it's a foreground CLI for a human/agent.
- No new env vars, no migration, no change to scheduled worker behavior
  (the only production-code touch is the additive `only_keywords` param on
  `run_flywheel_stage`).

## Testing

- Unit/DB: each adapter invoked with fakes (FakeExtractor / _NoAdExtractor
  style) — asserts the right stage fn is called with env-derived + overridden
  args; `--record` writes exactly one finished run row and default writes
  none; `only_keywords` filters the flywheel sweep to the named live keywords
  and skips mining/admission; JSON lands on stdout, exit codes correct.
- Live gate: in the deployed container, `run_stage flywheel --keywords
  "<one live kw>"` returns stats with keywords_swept=1 and writes no
  flywheel_runs row.
