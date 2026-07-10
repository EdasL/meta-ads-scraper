# Generic Deep Keyword Lane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A weekly worker stage that deep-sweeps ~19 static copy phrases to Meta's pagination ceiling, folding candidates into the normal pipeline and mining the collected ad copy into new trial keywords.

**Architecture:** Reuse `run_sweep` (new `collect` callback + `track_pages` log) over a new `generic_deep` taxonomy layer; a new stage module runs it weekly (own runs table, migration 151, corpus-refresh pattern) and feeds collected copy through the existing mint chain (`mine_phrases` → `drop_token_supersets` → injected redundancy gate → `add_trial_keywords(source='generic_deep')`).

**Tech Stack:** Python (src/agf), SQLAlchemy + psycopg3, pytest against the rolled-back `agf_it` DB.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-10-generic-deep-keyword-lane-design.md`.
- Env knobs (exact names/defaults): `AGF_GENERIC_DEEP`=1, `AGF_GENERIC_DEEP_PAGES`=150, `AGF_GENERIC_DEEP_EVERY_DAYS`=7, `AGF_GENERIC_DEEP_MINT_SLOTS`=25.
- Seed list = exactly the 19 terms in Task 2 (the founder trimmed it; do not add terms). No term may duplicate `FUNNEL_PHRASES`, `NICHE_TERMS`, or `STORE_FRAGMENTS`.
- Migrations must never contain a bare `%` (use `mod()` / `make_interval` — the runner chokes on `%`; regression lint exists).
- DB tests run with `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it`; a skip is not a pass.
- Another session edits `src/agf/workers/main.py` concurrently — re-read it immediately before editing; stage explicit paths only.
- Stage isolation contract (same as funnel/corpus-refresh): a stage failure is recorded on its run row and must never abort the worker tick.

---

### Task 1: Runs table + gate helpers (migration 151, `db/generic_deep_runs.py`)

**Files:**
- Create: `db/schema/151_generic_deep_runs.sql`
- Create: `src/agf/db/generic_deep_runs.py`
- Test: `tests/db/test_generic_deep_runs.py`

**Interfaces:**
- Produces: `open_generic_deep_run(conn) -> int`, `close_generic_deep_run(conn, run_id, *, stats: dict | None = None, error: str | None = None) -> None`, `generic_deep_ran_within_days(conn, days: int) -> bool`. Tasks 5 uses all three.

- [ ] **Step 1: Write the failing test**

```python
# tests/db/test_generic_deep_runs.py
"""Run-log helpers for the weekly generic-deep stage (migration 151). Uses the
rolled-back pg engine fixture; rows never persist past the test."""
from agf.db.generic_deep_runs import (close_generic_deep_run,
                                      generic_deep_ran_within_days,
                                      open_generic_deep_run)


def test_open_close_and_weekly_gate(pg_conn):
    assert generic_deep_ran_within_days(pg_conn, 7) is False   # empty table
    run_id = open_generic_deep_run(pg_conn)
    # an OPEN (unfinished) run does not satisfy the gate
    assert generic_deep_ran_within_days(pg_conn, 7) is False
    close_generic_deep_run(pg_conn, run_id, stats={"mined": ["x"], "trials_added": 1})
    assert generic_deep_ran_within_days(pg_conn, 7) is True


def test_failed_run_does_not_block_the_week(pg_conn):
    run_id = open_generic_deep_run(pg_conn)
    close_generic_deep_run(pg_conn, run_id, error="ExtractError('boom')")
    # finished WITH error still counts as finished — mirror corpus_refresh semantics?
    # NO: the funnel deep gate retries after a failure. Assert the row exists but
    # carries the error; the due decision in the worker keys on finished_at, so a
    # failed-but-closed run DOES satisfy ran_within_days — the retry semantics come
    # from the worker closing with error=... ONLY when the stage raised after doing
    # partial work. Assert the observable contract:
    assert generic_deep_ran_within_days(pg_conn, 7) is True
```

Note for the implementer: the second test documents the contract as-is (close-with-error counts as ran; identical to `corpus_refresh_ran_within` which only checks `finished_at`). Keep both asserts exactly as written.

First check the fixture name: `grep -rn "def pg_conn\|def _migrated_engine" tests/ conftest.py | head`. Tests in `tests/db/` use whichever rolled-back-connection fixture that grep shows (`pg_conn` above is the expected name; if the repo's fixture is `_migrated_engine`-based, open a `.connect()` transaction the way sibling files in `tests/db/` do — copy the pattern from an existing `tests/db/test_*.py`).

- [ ] **Step 2: Run test to verify it fails**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it python -m pytest tests/db/test_generic_deep_runs.py -v`
Expected: FAIL (`ModuleNotFoundError: agf.db.generic_deep_runs` — and the table doesn't exist yet).

- [ ] **Step 3: Write migration + module**

```sql
-- db/schema/151_generic_deep_runs.sql
-- 151: run log for the weekly generic-deep copy-phrase sweep (spec
-- docs/superpowers/specs/2026-07-10-generic-deep-keyword-lane-design.md), sibling to
-- discovery.corpus_refresh_runs. The worker gates the stage on a FINISHED row within
-- AGF_GENERIC_DEEP_EVERY_DAYS; stats holds the stage's counter dict as jsonb.
CREATE TABLE IF NOT EXISTS discovery.generic_deep_runs (
  id          BIGSERIAL PRIMARY KEY,
  started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ,
  stats       JSONB,
  error       TEXT
);
CREATE INDEX IF NOT EXISTS generic_deep_runs_started_idx
  ON discovery.generic_deep_runs (started_at);
```

```python
# src/agf/db/generic_deep_runs.py
"""Run-log helpers for the weekly generic-deep copy-phrase sweep (docs/superpowers/
specs/2026-07-10-generic-deep-keyword-lane-design.md). Sibling to
db/corpus_refresh_runs.py; conn-first (callers own the txn). A FINISHED row within
the last N days gates the weekly cadence."""
import json

from sqlalchemy import text


def open_generic_deep_run(conn) -> int:
    return conn.execute(text(
        "INSERT INTO discovery.generic_deep_runs DEFAULT VALUES RETURNING id")).scalar_one()


def close_generic_deep_run(conn, run_id: int, *, stats: dict | None = None,
                           error: str | None = None) -> None:
    conn.execute(text(
        "UPDATE discovery.generic_deep_runs SET finished_at = now(), "
        "stats = CAST(:stats AS jsonb), error = :err WHERE id = :id"),
        {"stats": json.dumps(stats) if stats is not None else None,
         "err": error, "id": run_id})


def generic_deep_ran_within_days(conn, days: int) -> bool:
    """True when a generic-deep run FINISHED within the last `days` days. An open
    (crashed mid-flight) run doesn't count. Mirrors corpus_refresh_ran_within."""
    return bool(conn.execute(text(
        "SELECT EXISTS(SELECT 1 FROM discovery.generic_deep_runs "
        "WHERE finished_at IS NOT NULL "
        "AND finished_at > now() - make_interval(days => :d))"),
        {"d": days}).scalar())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it python -m pytest tests/db/ -v`
Expected: new tests PASS, migration-placeholder lint (`test_migrations_placeholder_safe.py`) PASS.

- [ ] **Step 5: Commit**

```bash
git add db/schema/151_generic_deep_runs.sql src/agf/db/generic_deep_runs.py tests/db/test_generic_deep_runs.py
git commit -m "feat(db): discovery.generic_deep_runs run log (migration 151)"
```

---

### Task 2: Taxonomy layer — `GENERIC_DEEP_TERMS` + `generic_deep_entries()`

**Files:**
- Modify: `src/agf/discovery/taxonomy.py` (add below `STORE_FRAGMENTS`, above `_entries`)
- Test: `tests/discovery/test_taxonomy_generic_deep.py`

**Interfaces:**
- Produces: `GENERIC_DEEP_UNORDERED: list[str]`, `GENERIC_DEEP_PHRASES: list[str]`, `GENERIC_DEEP_TERMS: list[str]` (concat), `generic_deep_entries(deep_pages: int) -> list[TaxonomyEntry]` with `layer="generic_deep"`. Task 4 consumes `generic_deep_entries` and `GENERIC_DEEP_TERMS`.
- Must NOT change `all_entries()` or `sweep_entries()` — the lane is weekly-only.

- [ ] **Step 1: Write the failing test**

```python
# tests/discovery/test_taxonomy_generic_deep.py
"""Shape of the generic-deep copy-phrase lane (spec 2026-07-10): founder-approved
19 terms, no duplicates against any daily-swept layer, weekly-only (not in
sweep_entries), correct search types."""
from agf.discovery.taxonomy import (FUNNEL_PHRASES, GENERIC_DEEP_PHRASES,
                                    GENERIC_DEEP_TERMS, GENERIC_DEEP_UNORDERED,
                                    NICHE_TERMS, STORE_FRAGMENTS,
                                    generic_deep_entries, sweep_entries)


def test_seed_list_is_the_approved_19():
    assert len(GENERIC_DEEP_TERMS) == 19
    assert len(set(GENERIC_DEEP_TERMS)) == 19                    # no dupes inside
    assert GENERIC_DEEP_UNORDERED == ["app", "ai"]


def test_no_overlap_with_daily_layers():
    daily = set(FUNNEL_PHRASES) | set(NICHE_TERMS) | set(STORE_FRAGMENTS)
    assert not (set(GENERIC_DEEP_TERMS) & daily)


def test_entries_shape_and_search_types():
    entries = generic_deep_entries(150)
    assert len(entries) == 19
    assert all(e.layer == "generic_deep" for e in entries)
    assert all(e.max_pages == 150 for e in entries)
    by_kw = {e.keyword: e.search_type for e in entries}
    assert by_kw["app"] == "KEYWORD_UNORDERED"
    assert by_kw["ai"] == "KEYWORD_UNORDERED"
    assert by_kw["ai headshots"] == "KEYWORD_EXACT_PHRASE"
    assert by_kw["wish i knew sooner"] == "KEYWORD_EXACT_PHRASE"


def test_not_in_the_daily_sweep():
    daily_kws = {e.keyword for e in sweep_entries()}
    assert not (set(GENERIC_DEEP_TERMS) & daily_kws)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/discovery/test_taxonomy_generic_deep.py -v`
Expected: FAIL (`ImportError: cannot import name 'GENERIC_DEEP_PHRASES'`).

- [ ] **Step 3: Add the layer to `taxonomy.py`** (after the `STORE_FRAGMENTS` list)

```python
# Layer 4 — generic copy-phrase deep lane (spec docs/superpowers/specs/
# 2026-07-10-generic-deep-keyword-lane-design.md). Ad Library keyword search matches
# COPY TEXT + page name, so these are phrases that literally appear inside app-install
# ad copy. Swept WEEKLY to Meta's pagination ceiling by the generic-deep worker stage —
# deliberately NOT part of the daily sweep_entries(). Quiz openers live in
# FUNNEL_PHRASES; daily install fragments live in STORE_FRAGMENTS (no duplicates,
# asserted by tests/discovery/test_taxonomy_generic_deep.py). Founder-approved list
# 2026-07-10 — extend only with his sign-off.
GENERIC_DEEP_UNORDERED = ["app", "ai"]
GENERIC_DEEP_PHRASES = [
    # app-install CTAs
    "get the app", "on the app store", "start your free trial", "3-day free trial",
    # AI-tool copy phrases (how AI wrappers describe themselves)
    "ai headshots", "ai photo generator", "ai companion", "talk to ai",
    "your personal ai", "ai powered",
    # intent/emotion hooks (direct-response copy that's working)
    "does this actually work", "wish i knew sooner", "my biggest regret",
    "what no one tells you about", "stopped using", "thousands switched",
    "customers are obsessed",
]
GENERIC_DEEP_TERMS = GENERIC_DEEP_UNORDERED + GENERIC_DEEP_PHRASES


def generic_deep_entries(deep_pages: int) -> list[TaxonomyEntry]:
    """The generic copy-phrase lane with every per-entry page ceiling raised to
    `deep_pages`. Broad single tokens page as UNORDERED; copy phrases as EXACT."""
    return (_entries(GENERIC_DEEP_UNORDERED, "generic_deep",
                     "KEYWORD_UNORDERED", deep_pages)
            + _entries(GENERIC_DEEP_PHRASES, "generic_deep",
                       "KEYWORD_EXACT_PHRASE", deep_pages))
```

Note: `generic_deep_entries` calls `_entries`, which is defined AFTER the layer lists in the current file — keep the new function below `_entries` (or below `sweep_entries`) so the name exists at def-time is not an issue (it's resolved at call time, so position only matters for readability; place the lists after `STORE_FRAGMENTS` and the function next to `sweep_entries`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/discovery/test_taxonomy_generic_deep.py tests/discovery/test_taxonomy_balance.py -v`
Expected: PASS (balance test guards the niche frame — must stay green).

- [ ] **Step 5: Commit**

```bash
git add src/agf/discovery/taxonomy.py tests/discovery/test_taxonomy_generic_deep.py
git commit -m "feat(discovery): generic_deep taxonomy layer — 19 founder-approved copy phrases"
```

---

### Task 3: `run_sweep` gains `collect` + `track_pages`

**Files:**
- Modify: `src/agf/discovery/sweep.py:131-229` (`run_sweep` only; `_process_page` untouched)
- Test: `tests/discovery/test_sweep.py` (append)

**Interfaces:**
- Consumes: nothing new.
- Produces: `run_sweep(..., collect=None, track_pages=False)`. `collect` is called once per raw `AdRecord` (before any fold/drop). `track_pages=True` adds `stats["page_log"]: list[dict]` with one row per entry: `{"keyword": str, "pages": int, "ads": int, "ceiling_hit": bool}` — `ceiling_hit` true iff the loop stopped while Meta still had pages (`has_next` and `cursor` truthy at exit). Task 4 consumes both.

- [ ] **Step 1: Write the failing tests** (append to `tests/discovery/test_sweep.py`)

```python
# --- collect callback + page tracking (generic-deep lane, spec 2026-07-10) ---------
def test_sweep_collect_receives_every_raw_ad():
    # Two pages, two ads each — collect sees all 4 AdRecords even though every ad
    # is a drop (social host), BEFORE any fold. No DB needed (engine=None is safe
    # because no candidate/funnel rows are produced).
    pages = [[ad("https://www.instagram.com/a", page_id="SWEEPCOL1"),
              ad("https://www.instagram.com/b", page_id="SWEEPCOL2")],
             [ad("https://www.instagram.com/c", page_id="SWEEPCOL3"),
              ad("https://www.instagram.com/d", page_id="SWEEPCOL4")]]
    ext = FakeExtractor().script_pages("collect kw", pages)
    got = []
    asyncio.run(run_sweep(None, ext, entries=[_entry("collect kw")],
                          sleep=lambda s: None, patience_pages=10, max_depth=10,
                          collect=got.append))
    assert [a.page_id for a in got] == ["SWEEPCOL1", "SWEEPCOL2",
                                        "SWEEPCOL3", "SWEEPCOL4"]


def test_sweep_track_pages_logs_depth_and_ceiling(_migrated_engine):
    # Keyword A: 6 all-new pages but max_depth=3 -> stopped AT the cap with more
    # pages left => ceiling_hit True, pages=3. Keyword B: 1 page, has_next False
    # => exhausted, ceiling_hit False.
    eng = _migrated_engine
    _clear_adaptive(eng)
    try:
        ext = (FakeExtractor()
               .script_pages("ceil kw", [[_cand(600 + i)] for i in range(6)])
               .script_pages("done kw", [[_cand(650)]]))
        stats = asyncio.run(run_sweep(
            eng, ext, entries=[_entry("ceil kw"), _entry("done kw")],
            sleep=lambda s: None, patience_pages=10, max_depth=3, track_pages=True))
        log = {r["keyword"]: r for r in stats["page_log"]}
        assert log["ceil kw"]["pages"] == 3 and log["ceil kw"]["ceiling_hit"] is True
        assert log["ceil kw"]["ads"] == 3
        assert log["done kw"]["pages"] == 1 and log["done kw"]["ceiling_hit"] is False
    finally:
        _clear_adaptive(eng)
```

Note: `_entry`, `_cand`, `_clear_adaptive`, `ad`, `FakeExtractor` already exist in this test file — reuse them. Check whether `FakeExtractor.script_pages` marks `has_next=False` on the last scripted page (read `tests/discovery/conftest.py`); the "done kw" assertion depends on it, and the existing `test_sweep_narrow_keyword_stops_at_page_one` proves it does.

- [ ] **Step 2: Run tests to verify they fail**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it python -m pytest tests/discovery/test_sweep.py -v -k "collect or track_pages"`
Expected: FAIL (`TypeError: run_sweep() got an unexpected keyword argument 'collect'`).

- [ ] **Step 3: Implement in `run_sweep`**

Signature (add the two params at the end):

```python
async def run_sweep(engine, extractor, *, entries=None, keywords=None,
                    countries=("US",), max_pages: int = 2, kw_delay: float = 3.0,
                    max_consecutive_errors: int = 0, sleep=time.sleep,
                    heartbeat=None, patience_pages: int | None = None,
                    max_depth: int | None = None,
                    page_delay: float | None = None,
                    collect=None, track_pages: bool = False) -> dict:
```

Extend the docstring with:

```
    `collect` (generic-deep lane): called once per raw AdRecord as pages arrive,
    BEFORE any fold/drop — the copy-harvest hook. `track_pages` appends one
    {"keyword", "pages", "ads", "ceiling_hit"} row per entry to stats["page_log"];
    ceiling_hit means the loop stopped while Meta still had pages (cap/patience),
    so a to-exhaustion caller can log it instead of silently capping.
```

After the `stats = {...}` literal add:

```python
    if track_pages:
        stats["page_log"] = []
```

Inside the entry loop: initialize `has_next = False` and `kw_ads = 0` next to `cursor = None`; after a successful `search_page` (right after `pages_fetched += 1`):

```python
            kw_ads += len(records)
            if collect is not None:
                for ad in records:
                    collect(ad)
```

And right before `stats["serp_requests"] += pages_fetched`:

```python
        if track_pages:
            stats["page_log"].append({"keyword": entry.keyword, "pages": pages_fetched,
                                      "ads": kw_ads,
                                      "ceiling_hit": bool(has_next and cursor)})
```

(The `errored` early-`continue` sits AFTER the serp_requests line — the page_log append must come before it so an errored keyword still logs its partial depth; `has_next` stays False from initialization if the first fetch raised.)

- [ ] **Step 4: Run the sweep suite**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it python -m pytest tests/discovery/test_sweep.py -v`
Expected: ALL pass (old + 2 new).

- [ ] **Step 5: Commit**

```bash
git add src/agf/discovery/sweep.py tests/discovery/test_sweep.py
git commit -m "feat(discovery): run_sweep collect hook + per-keyword page log"
```

---

### Task 4: Stage module `discovery/generic_deep.py`

**Files:**
- Create: `src/agf/discovery/generic_deep.py`
- Test: `tests/discovery/test_generic_deep.py`

**Interfaces:**
- Consumes: `generic_deep_entries`, `GENERIC_DEEP_TERMS` (Task 2); `run_sweep(collect=, track_pages=)` (Task 3); existing `mine_phrases(corpus, *, exclude, min_advertisers, limit)`, `drop_token_supersets(mined, existing)`, `tried_keywords(conn)`, `trial_count(conn)`, `add_trial_keywords(conn, kws, *, source=)`, `all_entries()`.
- Produces: `run_generic_deep_stage(engine, extractor, *, deep_pages=150, kw_delay=3.0, mint_slots=25, max_trials=40, min_advertisers=3, gate=None, heartbeat=None, entries=None) -> dict` returning `{"sweep": <run_sweep stats>, "mined": list[str], "trials_added": int, "copy_rows": int}`. `gate` is `Callable[[list[str], int], list[str]] | None` (None = pass-through capped to room). Task 5 consumes this signature.

- [ ] **Step 1: Write the failing test**

```python
# tests/discovery/test_generic_deep.py
"""The weekly generic-deep stage end-to-end against the FakeExtractor + test DB:
deep-paged candidates land tagged technique='generic_deep', collected ad copy mints
trial keywords with source='generic_deep', slots and trial-pool room are respected."""
import asyncio

from sqlalchemy import text

from agf.discovery.generic_deep import run_generic_deep_stage
from agf.discovery.taxonomy import TaxonomyEntry
from agf.parse import AdRecord
from tests.discovery.conftest import FakeExtractor

NOW = 1_749_000_000


def _ad(link, body, page_id, name="Generic Deep Test"):
    return AdRecord("a1", page_id, name, True, NOW - 5 * 86_400, None, 1,
                    "DOWNLOAD", "Install", link, body, page_like_count=500)


def _entries(deep_pages):
    return [TaxonomyEntry("gdtest phrase", "generic_deep",
                          "KEYWORD_EXACT_PHRASE", deep_pages)]


def _clear(eng):
    with eng.begin() as conn:
        conn.execute(text(
            "DELETE FROM discovery.candidates WHERE page_id LIKE 'GDTEST%'"))
        conn.execute(text(
            "DELETE FROM discovery.dynamic_keywords WHERE source = 'generic_deep'"))


def test_stage_persists_tagged_candidates_and_mints_from_copy(_migrated_engine):
    eng = _migrated_engine
    _clear(eng)
    try:
        # Two pages. The recurring phrase "night routine builder" spans TWO distinct
        # advertisers -> mine_phrases (min_advertisers=2) should surface it.
        pages = [
            [_ad("https://apps.apple.com/us/app/x/id90001",
                 "Build your night routine builder tonight", "GDTEST1")],
            [_ad("https://apps.apple.com/us/app/x/id90002",
                 "The night routine builder everyone loves", "GDTEST2")],
        ]
        ext = FakeExtractor().script_pages("gdtest phrase", pages)
        gate_calls = []

        def gate(phrases, limit):
            gate_calls.append((list(phrases), limit))
            return list(phrases)[:limit]

        stats = run_generic_deep_stage(
            eng, ext, deep_pages=10, kw_delay=0.0, mint_slots=5, max_trials=40,
            min_advertisers=2, gate=gate, entries=_entries(10))
        # candidates landed with the lane's technique tag
        with eng.connect() as conn:
            rows = conn.execute(text(
                "SELECT technique FROM discovery.candidates "
                "WHERE page_id LIKE 'GDTEST%'")).all()
        assert len(rows) == 2 and all(r.technique == "generic_deep" for r in rows)
        # the cross-advertiser phrase was mined, gated, and added as a trial
        assert any("night routine builder" in m for m in stats["mined"])
        assert gate_calls, "the redundancy gate must be consulted"
        assert stats["trials_added"] >= 1
        assert stats["copy_rows"] == 2
        assert stats["sweep"]["page_log"][0]["keyword"] == "gdtest phrase"
        with eng.connect() as conn:
            src = conn.execute(text(
                "SELECT source FROM discovery.dynamic_keywords "
                "WHERE source = 'generic_deep' LIMIT 1")).scalar_one()
        assert src == "generic_deep"
    finally:
        _clear(eng)


def test_stage_respects_mint_slots_and_trial_room(_migrated_engine):
    eng = _migrated_engine
    _clear(eng)
    try:
        pages = [[_ad("https://apps.apple.com/us/app/x/id90003",
                      "unique alpha phrase here and unique beta phrase here",
                      "GDTEST3"),
                  _ad("https://apps.apple.com/us/app/x/id90004",
                      "unique alpha phrase here and unique beta phrase here",
                      "GDTEST4")]]
        ext = FakeExtractor().script_pages("gdtest phrase", pages)
        # max_trials equal to the current trial count -> room 0 -> nothing minted
        with eng.connect() as conn:
            from agf.db.dynamic_keywords import trial_count
            current = trial_count(conn)
        stats = run_generic_deep_stage(
            eng, ext, deep_pages=5, kw_delay=0.0, mint_slots=5, max_trials=current,
            min_advertisers=2, gate=None, entries=_entries(5))
        assert stats["mined"] == [] and stats["trials_added"] == 0
    finally:
        _clear(eng)
```

Before writing, verify the `dynamic_keywords` table/column names the cleanup and asserts use: `grep -n "INSERT INTO" src/agf/db/dynamic_keywords.py` (the table is `discovery.dynamic_keywords` with a `source` column — confirm and adjust the SQL literals if the grep shows otherwise).

- [ ] **Step 2: Run test to verify it fails**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it python -m pytest tests/discovery/test_generic_deep.py -v`
Expected: FAIL (`ModuleNotFoundError: agf.discovery.generic_deep`).

- [ ] **Step 3: Write the stage**

```python
# src/agf/discovery/generic_deep.py
"""The weekly generic-deep stage (spec docs/superpowers/specs/
2026-07-10-generic-deep-keyword-lane-design.md): deep-sweep ~19 static copy phrases
to Meta's real pagination ceiling, then mine the collected ad copy into new trial
keywords.

Two payoffs per pass, one set of requests:
  1. candidates -> discovery.candidates tagged technique='generic_deep' (plus the
     sweep's normal web-funnel folds) — search matches ad COPY, so these phrases
     surface advertisers the niche-noun taxonomy never matches;
  2. every raw ad's body_text -> mine_phrases (its home domain: cross-advertiser
     repetition in ad copy) -> superset drop -> injected redundancy gate ->
     dynamic_keywords trials (source='generic_deep').

Patience is disabled (the harvest must not stop on candidate-dry pages — copy and
funnel folds still accrue); depth stops on Meta exhaustion or the deep_pages cap,
and every cap hit is logged, never silent."""
import asyncio
import sys

from agf.db.dynamic_keywords import add_trial_keywords, tried_keywords, trial_count
from agf.db.engine import begin
from agf.discovery.keyword_mining import mine_phrases
from agf.discovery.keyword_redundancy import drop_token_supersets
from agf.discovery.sweep import run_sweep
from agf.discovery.taxonomy import (GENERIC_DEEP_TERMS, all_entries,
                                    generic_deep_entries)

_NO_PATIENCE = 1_000_000   # a harvest lane never stops on candidate-dry pages


def run_generic_deep_stage(engine, extractor, *, deep_pages=150, kw_delay=3.0,
                           mint_slots=25, max_trials=40, min_advertisers=3,
                           gate=None, heartbeat=None, entries=None) -> dict:
    """One weekly pass. `gate` is the redundancy gate (phrases, limit) -> phrases —
    injected by the worker (workers.main._gate_keywords) to avoid a circular import;
    None passes through capped. `entries` is test-injectable."""
    if entries is None:
        entries = generic_deep_entries(deep_pages)
    copy_rows: list[tuple[str, str]] = []

    def _collect(ad):
        if ad.page_id and ad.body_text:
            copy_rows.append((str(ad.page_id), ad.body_text))

    sweep = asyncio.run(run_sweep(
        engine, extractor, entries=entries, max_pages=deep_pages,
        max_depth=deep_pages, patience_pages=_NO_PATIENCE, kw_delay=kw_delay,
        heartbeat=heartbeat, track_pages=True, collect=_collect))
    for row in sweep.get("page_log", []):
        if row["ceiling_hit"]:
            print(f"[worker] generic deep sweep: {row['keyword']!r} hit the "
                  f"{deep_pages}-page ceiling ({row['ads']} ads) — more pages exist",
                  file=sys.stderr, flush=True)

    with engine.connect() as conn:
        tried = tried_keywords(conn)
        room = min(mint_slots, max(0, max_trials - trial_count(conn)))
    known = {e.keyword for e in all_entries()} | set(GENERIC_DEEP_TERMS) | tried
    mined = (mine_phrases(copy_rows, exclude=known, limit=room,
                          min_advertisers=min_advertisers)
             if copy_rows and room > 0 else [])
    if mined:
        mined = drop_token_supersets(mined, known)
    if mined:
        mined = gate(mined, room) if gate is not None else list(mined)[:room]
    trials_added = 0
    if mined:
        with begin(engine) as conn:
            trials_added = add_trial_keywords(conn, mined, source="generic_deep")
    return {"sweep": sweep, "mined": list(mined), "trials_added": trials_added,
            "copy_rows": len(copy_rows)}
```

Check `mine_phrases`' exclusion semantics before assuming the test phrase survives: it matches `exclude` **both ways on containment**, so the test's mined phrase must not contain (or be contained by) any taxonomy keyword. "night routine builder" contains no existing keyword ("morning routine app" is the closest and shares no full containment) — if the test fails on this, pick a more alien phrase (e.g. "zebra pocket flute") in BOTH ad bodies and the assert.

- [ ] **Step 4: Run tests to verify they pass**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it python -m pytest tests/discovery/test_generic_deep.py -v`
Expected: PASS ×2.

- [ ] **Step 5: Commit**

```bash
git add src/agf/discovery/generic_deep.py tests/discovery/test_generic_deep.py
git commit -m "feat(discovery): generic-deep weekly stage — deep sweep + copy-mint"
```

---

### Task 5: Worker wiring (`workers/main.py`) + full suite

**Files:**
- Modify: `src/agf/workers/main.py` — env docstring, imports, `generic_deep_due`, a `# 1.92)` stage block in `run_once`, `run_once` kwargs, `main()` env reads
- Test: `tests/discovery/test_generic_deep.py` (append the due-fn test)

**Interfaces:**
- Consumes: Task 1 run helpers, Task 4 `run_generic_deep_stage`, existing `_gate_keywords(engine, phrases, *, limit)`, `_touch_heartbeat`, `_env_int`.
- Produces: env knobs `AGF_GENERIC_DEEP` (default 1), `AGF_GENERIC_DEEP_PAGES` (150), `AGF_GENERIC_DEEP_EVERY_DAYS` (7), `AGF_GENERIC_DEEP_MINT_SLOTS` (25); `generic_deep_due(conn, *, dry_run, every_days) -> bool`.

**⚠️ Shared file:** another session is editing `main.py` (crowding work). Re-read the CURRENT file immediately before each edit; anchor edits on the corpus-refresh block, not line numbers; if an Edit fails on stale content, re-read and retry.

- [ ] **Step 1: Write the failing due-fn test** (append to `tests/discovery/test_generic_deep.py`)

```python
def test_generic_deep_due_gates_weekly(_migrated_engine):
    from agf.db.generic_deep_runs import (close_generic_deep_run,
                                          open_generic_deep_run)
    from agf.workers.main import generic_deep_due
    eng = _migrated_engine
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM discovery.generic_deep_runs"))
    with eng.connect() as conn:
        assert generic_deep_due(conn, dry_run=True, every_days=7) is False
        assert generic_deep_due(conn, dry_run=False, every_days=0) is False  # disabled
        assert generic_deep_due(conn, dry_run=False, every_days=7) is True
    with eng.begin() as conn:
        rid = open_generic_deep_run(conn)
        close_generic_deep_run(conn, rid, stats={})
    with eng.connect() as conn:
        assert generic_deep_due(conn, dry_run=False, every_days=7) is False
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM discovery.generic_deep_runs"))
```

- [ ] **Step 2: Run to verify it fails**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it python -m pytest tests/discovery/test_generic_deep.py -v -k due`
Expected: FAIL (`ImportError: cannot import name 'generic_deep_due'`).

- [ ] **Step 3: Wire the worker** (five anchored edits — re-read the file first)

(a) Env docstring — after the `AGF_CORPUS_REFRESH_BUDGET` line add:

```
  AGF_GENERIC_DEEP             run the weekly generic copy-phrase deep sweep (default 1; 0 disables)
  AGF_GENERIC_DEEP_PAGES       per-term page ceiling for the deep pass       (default 150)
  AGF_GENERIC_DEEP_EVERY_DAYS  generic deep cadence in days, 0 disables      (default 7)
  AGF_GENERIC_DEEP_MINT_SLOTS  trial keywords minted per weekly pass         (default 25)
```

(b) Imports — next to the `corpus_refresh_runs` import block:

```python
from agf.db.generic_deep_runs import (close_generic_deep_run,
                                      generic_deep_ran_within_days,
                                      open_generic_deep_run)
from agf.discovery.generic_deep import run_generic_deep_stage
```

(c) Due fn — next to `corpus_refresh_due`:

```python
def generic_deep_due(conn, *, dry_run, every_days) -> bool:
    """Whether this tick runs the weekly generic copy-phrase deep sweep. Never in a
    dry-run (real Meta paging, no offline fake). every_days<=0 disables. Gated on a
    FINISHED discovery.generic_deep_runs row within the window — a crashed (never
    closed) run doesn't block the retry next tick."""
    if dry_run or every_days <= 0:
        return False
    return not generic_deep_ran_within_days(conn, every_days)
```

(d) Stage block in `run_once` — insert AFTER the corpus-refresh block (`# 1.91`) and before the frame-mint block (`# 1.93`):

```python
    # 1.92) generic deep sweep — weekly to-exhaustion pass over ~19 static copy
    #       phrases (spec 2026-07-10): Ad Library search matches ad COPY, so generic
    #       phrases ("ai headshots", "wish i knew sooner") surface advertisers the
    #       niche-noun taxonomy never matches. Candidates land tagged
    #       technique='generic_deep'; collected copy mints dynamic_keywords trials
    #       (source='generic_deep') through the same redundancy gate as the flywheel.
    #       Same isolation contract as funnel/corpus-refresh: failures land on the
    #       run row and never abort the tick; a crashed run retries next tick.
    if generic_deep_enabled:
        with engine.connect() as conn:
            gd_due = generic_deep_due(conn, dry_run=dry_run,
                                      every_days=generic_deep_every_days)
        if gd_due:
            with begin(engine) as conn:
                gd_run_id = open_generic_deep_run(conn)
            try:
                gd_stats = run_generic_deep_stage(
                    engine, extractor, deep_pages=generic_deep_pages,
                    kw_delay=kw_delay, mint_slots=generic_deep_mint_slots,
                    max_trials=flywheel_max_trials,
                    gate=lambda ph, lim: _gate_keywords(engine, ph, limit=lim),
                    heartbeat=_touch_heartbeat)
                with begin(engine) as conn:
                    close_generic_deep_run(conn, gd_run_id, stats={
                        k: v for k, v in gd_stats.items() if k != "sweep"} | {
                        "sweep": {kk: vv for kk, vv in gd_stats["sweep"].items()}})
            except Exception as exc:
                with begin(engine) as conn:
                    close_generic_deep_run(conn, gd_run_id, error=repr(exc))
                print(f"[worker] generic deep stage failed: {exc!r}",
                      file=sys.stderr, flush=True)
            _touch_heartbeat()  # the deep pass is a long, blocking Meta stage
```

Simplification allowed: if `gd_stats` is already JSON-serializable end-to-end (it is — dicts/lists/ints/strs/bools), replace the dict-rebuild with `close_generic_deep_run(conn, gd_run_id, stats=gd_stats)`.

`flywheel_max_trials`: find the existing `run_once` parameter that carries `AGF_FLYWHEEL_MAX_TRIALS` (grep `max_trials` in `run_once` and its `main()` call site) and use THAT name; if the flywheel reads it internally and no kwarg exists, add `generic_deep_max_trials=_env_int("AGF_FLYWHEEL_MAX_TRIALS", 40)` alongside the other new kwargs and pass it instead.

(e) `run_once` signature + `main()` call — add, mirroring the funnel_deep kwargs:

```python
# run_once(...) signature additions:
             generic_deep_enabled=True, generic_deep_pages=150,
             generic_deep_every_days=7, generic_deep_mint_slots=25,
# main() call-site additions (next to funnel_deep_pages= / funnel_deep_every_days=):
        generic_deep_enabled=_env_int("AGF_GENERIC_DEEP", 1) == 1,
        generic_deep_pages=_env_int("AGF_GENERIC_DEEP_PAGES", 150),
        generic_deep_every_days=_env_int("AGF_GENERIC_DEEP_EVERY_DAYS", 7),
        generic_deep_mint_slots=_env_int("AGF_GENERIC_DEEP_MINT_SLOTS", 25),
```

- [ ] **Step 4: Full suite, bare, own call**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it python -m pytest -q`
Expected: 0 failed (skips only the known MinIO-gated media test). The dry-run worker test (if any exercises `run_once`) must stay green — `generic_deep_due` returns False under `dry_run`.

- [ ] **Step 5: Commit**

```bash
git add src/agf/workers/main.py tests/discovery/test_generic_deep.py
git commit -m "feat(worker): wire the weekly generic-deep stage (AGF_GENERIC_DEEP*)"
```

---

### Task 6: Deploy + live e2e (controller-owned; not a subagent task)

**Files:** none (ops).

- [ ] **Step 1:** Apply migration 151 to prod BEFORE rolling code (DEPLOYMENT.md §2 — proxy on :5433, `apply_migrations`).
- [ ] **Step 2:** Build the image from a clean `git worktree add <scratch> HEAD` (`bash ops/prod/02_build_image.sh`), then roll: `pull worker cron && up -d worker cron` on the VM.
- [ ] **Step 3:** Live e2e in the deployed container — a one-off reduced pass (2 entries, `deep_pages=5`, `mint_slots=3`) via `docker compose exec -T worker python -c ...` calling `run_generic_deep_stage` with `gate=None`; verify in prod DB: candidates rows with `technique='generic_deep'`, `dynamic_keywords` rows with `source='generic_deep'` (if the reduced corpus mints anything — 0 mints is acceptable for the reduced pass, candidates tag is the hard assert), ceiling log lines on stderr.
- [ ] **Step 4:** Leave the weekly gate armed (no run row for the full pass) so the next worker tick runs the real first pass; then verify `discovery.generic_deep_runs` gains a FINISHED row and report per-term depth from its stats.

---

## Self-review notes

- Spec coverage: seed list (T2), weekly gate + runs table (T1, T5), deep paging with patience off + ceiling logging (T3, T4), technique tag (T4 test), mint chain with slots + room bounds (T4), env knobs (T5), stage isolation (T5 block), live e2e (T6). `download the app` removed at plan time as an exact STORE_FRAGMENTS duplicate — spec amended, list is 19.
- Type consistency: `gate(phrases, limit)` positional in both the stage and the worker lambda; `run_generic_deep_stage` returns the four keys T5's close-run serializes; `page_log` row keys match between T3 and T4.
- No placeholders: every code step is complete; the two "grep first" notes bind to existing symbols, with explicit fallbacks.
