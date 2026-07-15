# Hunt round: never discard a completed scrape — design

Date: 2026-07-15. Trigger: round 27 (page_cap 100, patience 10, 11 terms, 7
competitors) FINISHED its scrape — "Search finished — 2802 found, 460 new", 821
results persisted — then died at 1799s **mid-triage/judge**, was marked `error`,
and the 821 results were orphaned under a "failed" round. Raising
`AGF_HUNT_ROUND_TIMEOUT_SEC` 1800→5400 (shipped b8d0262) buys headroom but does
not fix the root cause: **a single SIGALRM covers scrape + finish, and any finish
overrun throws away completed scrape work.**

## Root cause

`hunt_one.py` arms one `signal.alarm(timeout)` around the entire
`run_hunt_round`, which does: sweep (phases 0–4, results persisted incrementally,
each page its own txn) THEN phase 5 (triage → relevance judge → enrich enqueue →
keyword mint). The judge makes batched LLM calls and triage makes store-listing
calls, both scaling with result-set size — so a big scrape's phase 5 is itself
minutes long. When the alarm fires anywhere in phase 5, `HuntTimeout` →
`_fail` → round `error`, even though every result is already committed.

Results are durable the moment each page commits. Phase 5 is *enrichment of the
record*, not the record itself. Nothing in phase 5 should be able to fail the
round.

## Design — separate the scrape budget from the finish

Split the one alarm into two phases with independent budgets, and make the
finish best-effort:

1. **Scrape budget** (`AGF_HUNT_SCRAPE_BUDGET_SEC`, default = round timeout minus
   finish budget): armed around the sweep only. On expiry, `HuntTimeout` unwinds
   the sweep as today, but the round is committed **`done`** (partial scrape —
   results so far are real), NOT `error`. Add a `partial` marker to
   progress/telemetry so the UI/Peon can say "stopped early at the page budget".
2. **Finish budget** (`AGF_HUNT_FINISH_BUDGET_SEC`, default ~600s): after the
   sweep returns (or partial-times-out), disarm the scrape alarm, then run phase 5
   under its own alarm. A finish-budget timeout is caught and swallowed: commit
   the round `done` with whatever triage/judge completed. Un-judged kept rows keep
   NULL relevance (already fail-open = relevant); un-enriched kept advertisers are
   still enqueued (move the `enqueue_enrich` call to run as early as the kept set
   is known, so a judge overrun can't skip it — or make enrich enqueue idempotent
   and run it in a `finally`).
3. **Lease/subprocess** stay `scrape + finish + margin` so the daemon's outer
   bound still dominates both inner alarms (preserve the ordering invariant
   SIGALRM < subprocess < lease).

Net: a round is `error` only when the sweep itself wedges before finishing (real
failure — proxy wedge, etc.); a completed scrape is ALWAYS `done`, and the finish
enhances it as far as the finish budget allows.

## Alternative considered (rejected)

"Just raise the one timeout" — shipped as the stopgap, but any scrape big enough
will still risk dying in the finish; it moves the cliff, doesn't remove it.

## Implementation sketch

- `hunt_one.py`: two `signal.alarm` windows; catch `HuntTimeout` from the finish
  window separately from the scrape window; the scrape-window handler commits
  `done`+partial, the finish-window handler commits `done` and logs the truncated
  finish. `signal.alarm(0)` between windows (already the pattern before the
  completion hook).
- `run_hunt_round`: return after the sweep so the caller owns phase 5 under the
  finish alarm — OR pass both budgets in and let it arm/disarm internally. Prefer
  the caller-owns-phase-5 split so the alarm boundaries live in one place
  (`hunt_one`).
- Move `enqueue_enrich` so a judge overrun can't skip enrich for already-kept
  rows (enrich is the founder-visible payoff).
- `compose_round_message` / progress: a `partial` round says so plainly (product
  voice), e.g. "Stopped at the page budget — N results kept." No fabricated
  numbers.

## Verification

- Unit: fake a phase-5 that overruns → round commits `done`, results intact,
  enrich enqueued. Fake a sweep that overruns → `done` + partial marker, no phase
  5 required to have run. Fake a sweep wedge (no pages) → `error` (real failure
  still fails).
- Live: a maxed round (page_cap 100) that would previously die in judge now
  finishes `done` with its results; a deliberately tiny finish budget forces the
  finish-timeout path and the round still lands `done`.
- Full Python + Go suites to 0 failed; no migration (telemetry-only progress
  additions).

## Out of scope

- Competitor-resolution accuracy (domain-based resolver — separate follow-up per
  docs/research/2026-07-15-hunt-term-strategy.md).
- Parallelizing phase-0 competitor pulls across extractors (a throughput tuning
  lever, not correctness).
