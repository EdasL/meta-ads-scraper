# Throughput-first scraper pool — design

Date: 2026-07-11  
Status: design approved conversationally; implementation not started

## Goal

Increase discovery throughput and candidate yield without reducing keyword coverage,
pagination depth, or deep-sweep budgets. The scraper should complete more Meta page
requests per hour while preserving per-proxy safety and making long-running work unable
to starve fresh discovery.

## Evidence and constraints

The current implementation has partial concurrency in
`src/agf/discovery/sweep.py`, but it partitions keywords into fixed round-robin shards.
The slowest shard determines wall-clock completion, and completed workers cannot claim
unfinished pages from another shard. Only the static sweep and flywheel use this
parallel shape; corpus refresh, funnel work, and generic deep work remain serial in
`run_once`.

The fetch path uses stateful curl_cffi sessions and proxy-bound cookies/tokens. A proxy
is therefore the natural isolation boundary: each lane owns one warm session and must
have at most one active Meta request at a time unless live measurements explicitly
prove a higher safe limit.

The current adaptive-depth behavior is valuable and must remain. Static keywords may
continue to page to the configured depth cap, flywheel keywords retain their lifecycle
rules, and generic deep sweeps retain their high page ceiling with patience disabled.
This design must not “solve” latency by lowering pages, keywords, or budgets.

## Proposed architecture

Use one shared in-process scraper pool with typed jobs and weighted scheduling. The
pool owns the Meta-facing proxy lanes; discovery stages submit jobs to it rather than
running their own serial loops.

```text
stage producers
  ├─ breadth keyword jobs
  ├─ flywheel keyword jobs
  ├─ generic/funnel deep jobs
  └─ corpus advertiser jobs
          │
          ▼
shared scheduler ── weighted fair queue ── proxy lane 1 ── warm session 1
                                     ├── proxy lane 2 ── warm session 2
                                     └── proxy lane N ── warm session N
          │
          ▼
batched result writer → candidates / dynamic keywords / ads / run logs
```

This is a shared pool, not separate infrastructure for each sweep type. Job type is
metadata used for priority, fairness, retry policy, and persistence. The first phase
can run inside the existing worker process; the job interfaces must be shaped so the
same fetcher can later consume durable `pipeline_jobs` rows.

## Job model

Each job represents the smallest independently schedulable network unit:

- `keyword_page`: keyword, search type, country list, cursor, depth, lane/source;
- `flywheel_page`: same page payload plus dynamic-keyword id and lifecycle attempt;
- `deep_page`: keyword or funnel phrase, cursor, depth, deep-run id;
- `corpus_page`: advertiser id, page id, cursor, depth, listing mode.

Jobs carry only identifiers, cursors, and bounded metadata. They do not hold open DB
transactions or proxy sessions. A successful page result includes records, next cursor,
`has_next`, request timing, proxy/lane id, and retry count. The scheduler enqueues the
next page only when pagination remains and the job's depth policy permits it.

## Scheduling and fairness

Use a dynamic queue rather than fixed keyword shards. A lane claims the next eligible
job whenever it finishes its current request, so a slow keyword cannot strand other
capacity.

Use weighted fair scheduling with these priorities:

1. breadth discovery and newly admitted flywheel trials;
2. normal active flywheel re-sweeps;
3. corpus refresh and funnel confirmation;
4. generic/funnel deep harvest work.

The scheduler must reserve a small share of capacity for deep work so it continues to
increase total coverage, while preventing one 150-page deep job from blocking fresh
keywords. A job yields after one page; it does not monopolize a lane until its keyword
is exhausted.

Concurrency is derived from the configured proxy set. Start with one lane per distinct
proxy endpoint. If no proxy is configured, use one direct lane. Do not create multiple
lanes over the same endpoint by default. Increasing per-proxy concurrency requires
observed latency, 429, challenge, and error-rate evidence.

## Completeness and ordering

The pool must preserve the current sweep semantics:

- every eligible keyword is submitted exactly once per scheduled pass;
- every returned cursor is either scheduled or recorded as a terminal failure;
- depth, patience, exhaustion, and lifecycle rules are evaluated per keyword;
- candidate deduplication remains database-backed and order-independent;
- `first_seen_depth` remains the shallowest observed depth;
- a failed page does not silently mark a keyword complete;
- a retryable failure requeues the same page with bounded attempts;
- a terminal failure is recorded with the cursor/depth so the pass is auditable.

Deep jobs are resumable. If the process exits after page 37 of a 150-page job, the
next run resumes from the last durable cursor rather than restarting the phrase from
page 1.

## Proxy, timeout, and retry behavior

Each lane owns its extractor/session pool and proxy binding. Existing classified
retryable errors and circuit-breaker behavior remain the foundation.

Every Meta page request needs a verified wall-clock ceiling outside the curl call. A
stuck proxy tunnel must abandon that page, release the lane, and requeue or fail the
job; it must not wait for the process watchdog. The ceiling, attempts, and backoff are
separate from the worker heartbeat budget.

The scheduler records per-request latency and error class. It temporarily cools a lane
after repeated timeout, rate-limit, session, or challenge errors, while healthy lanes
continue processing. A gateway-wide breaker pauses new Meta work only when all lanes
show the failure pattern.

## Persistence

The first implementation may keep the existing per-page candidate semantics, but move
DB writes behind a bounded writer path:

- fetch lanes never hold a write transaction while making network calls;
- page results are deduplicated in memory before persistence;
- the writer batches compatible candidate/ad upserts where safe;
- run counters and page-level telemetry are committed with the result;
- writer backpressure pauses job claims instead of growing unbounded memory.

The job interface must be compatible with the existing Postgres `pipeline_jobs` pattern:
short claim transaction, work outside the transaction, lease/retry state, and reaping
of abandoned work. Durable queue migration is a follow-up phase, not a prerequisite for
the first in-process throughput improvement.

## Observability

Every pass and job must expose:

- pages requested, pages completed, pages terminally failed;
- keywords/jobs completed and remaining;
- active lanes and per-lane proxy identity hash;
- request latency percentiles;
- retry, timeout, rate-limit, session, and challenge counts;
- candidates new/seen and raw ads processed;
- queue wait time versus network time versus persistence time.

The success metric is throughput at stable safety rates: completed Meta pages per hour,
new candidates per hour, and candidate yield per 1,000 requests. A faster run that only
produces more retries or challenges is not a success.

## Rollout

### Phase 1 — dynamic in-process pool

Replace fixed shard execution for keyword/page sweeps with the shared scheduler and
proxy lanes. Keep existing stage entry points and database schemas. Add tests for job
completeness, dynamic load balancing, proxy isolation, retry/requeue, and depth
preservation. Run a bounded live pass with the current proxy count and compare the
metrics above against the existing implementation.

### Phase 2 — typed priority lanes

Submit flywheel, corpus, funnel, and generic deep work through the same pool. Apply
weighted fairness and per-type budgets. Keep deep coverage unchanged while making all
long jobs yield after each page.

### Phase 3 — durable queue consumers

Move the scheduler state to Postgres jobs with leases and reaping. Split compute-only
work from Meta fetch work so scoring, embeddings, and triage can run while the scraper
continues. Preserve the same job payloads and fetcher contracts from Phase 1.

## Testing and acceptance

Before implementation, tests must prove:

- a slow keyword does not leave healthy lanes idle;
- all keywords and cursors are processed exactly once or recorded as failed;
- page depth and deep-sweep ceilings are unchanged;
- one proxy lane never uses another lane's session or proxy;
- retries do not duplicate persisted candidates;
- a stuck request releases its lane within the configured ceiling;
- deep work receives capacity while fresh flywheel work remains responsive;
- the existing focused suite remains green.

Live acceptance requires a real proxy-backed pass, not only fakes: compare wall-clock
duration, completed pages, 429/challenge rate, retry rate, and new-candidate yield at
the same coverage settings. Increase lane count only when the observed safety metrics
remain stable.

## Non-goals

- reducing keyword count, page depth, or deep-sweep budgets;
- replacing curl_cffi with an async client before proxy isolation is live-verified;
- adding Redis, Kafka, Celery, or another queue dependency;
- changing candidate scoring, lifecycle, or feed semantics;
- hiding failed pages behind a successful-looking run summary.
