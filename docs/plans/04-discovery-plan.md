# Discovery Firehoses Implementation Plan (Plan 4 of 7) — as built

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. Built from master-plan §6 (revised P4 task list, Firehose A dropped) + §3 contracts; documents the **as-built, verified** result.

**Goal:** the keyword-free discovery firehoses that feed the spine — **B (Meta Ad Library EU impressions radar + EU→US bridge)** and **C (funnel-tech fingerprint)** — plus the app↔advertiser linkage and the **mandatory 14-day scaling-confirm**. Consumes the Plan 2 `Extractor` facade (§3.4) and the Plan 3 `conn`-first repo (§3.3). **Firehose A (app-store new-apps seed) was dropped** (founder decision 2026-06-04, master-plan §8) — iTunes is enrichment-only.

**Architecture:** firehoses are async (they `await extractor.search/resolve_deeplink`); discovery logic is pure and unit-tested against a `FakeExtractor` + fixture `AdRecord`s, while DB routing is verified against a live Postgres via `pg_conn` (skip-clean without `AGF_TEST_DB_URL`). Discovery only *surfaces* candidates; the 14-day Ad-Library cross-check (`confirm_scaling_on_meta`) is what makes something count as "scaling on Meta."

**Tech stack:** Python 3.13 · reuses Plan 1 (`AdRecord`, `classify_url`, `scale.aggregate_advertisers`/`longevity_days`), Plan 2 (`Extractor`), Plan 3 (`repo`). No new deps (signature detection is stdlib; fingerprint client reuses httpx).

**Status:** ✅ **done — 15 tests green against live Postgres (114 across Plans 1–4); skips clean without infra.**

---

## Modules (src/agf/discovery/) and contracts

- **`seeds.py`** — `Candidate` (source/kind + page/app/funnel fields) and `upsert_candidate(conn, c)` router → app→`repo.upsert_app`, page→`repo.upsert_advertiser` (dedup-safe via ON CONFLICT). *(P4-T1)*
- **`firehose_eu.py`** — `run_eu_impressions_firehose(extractor, countries, max_pages)` (empty query, `sort_by_impressions`, DE/GB) + `collapse_to_advertisers(ads, now_epoch, min_running_days=25)` (Plan 1 aggregate → drop short-lived). *(P4-T4)*
- **`bridge.py`** — `bridge_eu_to_us(extractor, page_id)` (`search_type='page'` + `view_all_page_id`) + `run_bridge(extractor, page_ids)` → page + app Candidates. *(P4-T5)*
- **`firehose_fingerprint.py`** — `SIGNATURES` (paywall ∩ pixel ∩ mmp), `detect_signatures`, `classify_funnel_intent` (paywall **and** pixel), `fingerprint_search(client, query)`/`parse_publicwww` (PublicWWW source-code search). *(P4-T6)*
- **`link_apps.py`** — `link_app_to_advertiser(conn, extractor, candidate)`: search Meta, `resolve_deeplink`, match `store_id` → wire `ads.app_id`+`advertiser_id`, return `page_id`. *(P4-T7, master-plan §7 #5)*
- **`confirm.py`** — `confirm_scaling_on_meta(extractor, page_id, now_epoch, min_longevity_days=14)` + `run_scaling_confirm(...)`. The non-negotiable cross-check (§2). *(P4-T8)*
- **`run_discovery.py`** — `run_discovery(conn, extractor, now_epoch, …)` orchestrator B(+bridge)→C→upsert→link→confirm; `make_discovery_handler` adapter (§3.9); `--dry-run` CLI. *(P4-T9)*

## Task → file → verify map

| Task | Files | Verify (green) |
|---|---|---|
| P4-T1 | `seeds.py` | `pytest tests/discovery/test_seeds.py` (DB) |
| P4-T4 | `firehose_eu.py` | `pytest tests/discovery/test_firehose_eu.py` |
| P4-T5 | `bridge.py` | `pytest tests/discovery/test_bridge.py` |
| P4-T6 | `firehose_fingerprint.py` | `pytest tests/discovery/test_firehose_fingerprint.py` |
| P4-T7 | `link_apps.py` | `pytest tests/discovery/test_link_apps.py` (DB) |
| P4-T8 | `confirm.py` | `pytest tests/discovery/test_confirm.py` |
| P4-T9 | `run_discovery.py` | `pytest tests/discovery/test_run_discovery.py` (DB) + `python -m agf.discovery.run_discovery --dry-run` |
| ~~P4-T2/T3~~ | — | **DROPPED** (Firehose A; master-plan §8) |

## Test design
- **`FakeExtractor`** (`tests/discovery/conftest.py`): async stand-in for the §3.4 `Extractor` — `search` keyed by `view_all_page_id` / `sort_by_impressions` / `query`; `resolve_deeplink` defaults to `classify_url`. `make_ad(..., days_old=)` builds `AdRecord`s with deterministic longevity vs a fixed `NOW_EPOCH`.
- Pure firehose logic (collapse, classify, bridge, confirm) needs no infra; `seeds`/`link`/orchestrator routing run on `pg_conn` and skip clean without it.
- Opt-in live integration is deferred to the Phase 7 e2e (a real `Extractor()` run is exercised by Plan 2's integration tests already).

## How to run / verify
```bash
docker compose -f db/docker-compose.yml up -d postgres
export AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf
.venv/bin/pytest tests/discovery -q            # 15 passed
.venv/bin/python -m agf.discovery.run_discovery --dry-run
```

## Notes / decisions
- **Firehose A dropped** → the only structural gap is a strictly-US-only *app* advertiser that never runs EU ads and isn't a web funnel (accepted, §8). Firehose C (fingerprint) still catches web-native US-only funnels.
- `run_scaling_confirm` returns the **confirmed page_ids**; the orchestrator counts only those (the "never auto-call / human-review" gate is Plan 7). No invented `eligible` column — confirmation is a filter, `is_emerging` stays an app-age flag (Plan 1/6).
- `collapse_to_advertisers` reuses Plan 1 `aggregate_advertisers` (active-only) so the EU radar and the PoC share one definition of "advertiser scale."
- Discovery can run as a worker via `make_discovery_handler` (adapter §3.9) under Plan 3's `run_worker`.

## Live end-to-end (2026-06-04) — and a gap it caught
Ran the assembled pipeline for real (live Meta Ad Library `Extractor` + real Postgres, scaled down): DE impressions sweep → 10 real ads → 2 advertisers → EU→US bridge → 14-day confirm → rows committed. It **worked** (advertisers written, a real page confirmed scaling), and it **surfaced a gap the fake-extractor unit tests didn't**: `upsert_candidate` persisted advertisers/apps but not the discovered `ads`, so a web-funnel advertiser would have no `ads` rows (which Plan 5 velocity needs). **Fixed:** `upsert_candidate` now persists `c.ad_record` via `upsert_ad` when present (regression test `test_candidate_with_ad_record_persists_the_ad`); re-run confirmed `advertisers=2, ads=1`.

## Phase-Verify (met)
`pytest tests/discovery -q` green (16 tests); orchestrator upserts from B + bridge, persists discovered ads, links apps→advertisers, and gates on the 14-day confirm (`test_run_discovery_counts_only_confirmed`); `--dry-run` exits 0; live e2e writes real rows to Postgres.
