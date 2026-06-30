# Scraping-health MCP — design

**Date:** 2026-06-30
**Status:** approved, building
**Scope:** read-only observability MCP for the discovery/scraping engine.

## Why

Checking prod scraping health today meant: start the stopped `cloud-sql-proxy`,
re-auth expired ADC, rediscover column names (`first_seen_at` not `first_seen`,
`store` not `platform`, no `category` on the feed). That friction repeats every
session. An MCP encapsulates the connection + the curated metrics so the two
questions that matter — **is it finding new stuff** and **are the app ideas
diverse** — are one call away.

Two areas only (read-only): **discovery throughput** and **idea diversity**.
Funnel-conversion and keyword-yield are deliberately out of scope (addable later
as new tools without touching the core).

## Architecture

Python MCP server under `src/agf_mcp/`, reusing the repo `.venv` (Python 3.13,
SQLAlchemy 2.0, psycopg 3, numpy 2.2, scipy 1.15). Three layers:

- **`db.py`** — connection + safety. Reads `AGF_DATABASE_URL`. Every query runs
  in a `READ ONLY` transaction. Operational errors are classified into
  actionable messages instead of stack traces:
  - connection refused on `:5433` → attempt `launchctl kickstart` of
    `com.fielddesk.sqlproxy`, then retry once.
  - `invalid_rapt` / ADC token error → return "run `gcloud auth
    application-default login`".
  - anything else → short error string.
  Connection URL is a parameter so tests can target the test DB (`:55433`).
- **`metrics.py`** — the curated SQL + the embedding diversity computation.
  Schema gotchas baked in. No raw SQL is accepted from the model.
- **`server.py`** — registers the tools via the `mcp` SDK (FastMCP).

Registered through a project-level `.mcp.json`.

## Tools (4)

1. **`engine_pulse()`** — alive vs wedged. Newest candidate `first_seen_at`;
   last `sweep_runs` / `flywheel_runs` / `funnel_sweep_runs` timestamps + 24h/7d
   counts; triage backlog (`status='new'`).
2. **`discovery_rate(days=14)`** — new candidates/day series; status mix
   (new/kept/dropped) + kept-rate; distinct `source_keyword` breadth in window.
3. **`idea_diversity(days=30)`** — semantic, from `discovery.candidate_embeddings`
   (Gemini 768d on store listings), computed in Python:
   - **Effective # of concepts** — `exp(Shannon entropy)` over cluster sizes.
   - **Concentration** — top-1 / top-5 cluster share, for the whole corpus and
     for gems (`gem_score >= 60`).
   - **Concept groups** — scipy-clustered (kmeans2), each labeled by its
     representative app (nearest to centroid) + size.
   - **Novelty over time** — for finds in the last `days`, nearest-neighbor
     cosine distance to the prior corpus (rising = broadening, falling =
     rehashing).
   - small platform (`store`) split cross-check.
4. **`scraping_overview()`** — combines the above into one compact dashboard.

## Data caveats

- Filter to valid vectors only: `dim = 768` (drop `dim=0` / `model='unfetchable'`
  placeholder rows).
- Gem-tier joins: `candidate_embeddings.candidate_id` →
  `candidates.kept_advertiser_id` → `frontend.emerging_scaling_products.advertiser_id`.
- Embeddings cover kept candidates (100% of kept at design time: 1924/1924,
  minus unfetchable placeholders).

## Dependencies

Only new dep: the `mcp` Python SDK. Clustering uses numpy + scipy (already
present). No scikit-learn.

## Testing

pytest against the test DB (`AGF_TEST_DB_URL`, `localhost:55433`):
- each tool executes and returns the documented shape/keys;
- read-only enforcement: a write attempt raises;
- `idea_diversity` math validated on a small synthetic embedding set
  (deterministic seed) — effective-concept and novelty numbers behave.

## Out of scope (later, additive)

Funnel-conversion rates, per-keyword yield × like-rate, any write/control action.
