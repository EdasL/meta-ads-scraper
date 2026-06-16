# Corpus Ad Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Re-scrape every advertiser's live Meta `ALL` listing to refresh GemScore v3 running-days (`is_active`/`start_date`/`end_date`), reusing the proven `pull_keeper_history` fetch+upsert.

**Architecture:** Extract the per-advertiser body of `pull_keeper_history` into a shared `refresh_one_advertiser`. Add a corpus-wide selection + budgeted loop (`corpus_refresh.py`) and a one-shot runner script. Surface pagination-exhaustion from the search so a "trust the listing" `is_active` flip only fires on complete listings.

**Tech Stack:** Python, SQLAlchemy core, asyncio, pytest (DB tests use the `pg_conn`/`_migrated_engine` fixtures; set `AGF_TEST_DB_URL`).

**Spec:** `docs/superpowers/specs/2026-06-16-corpus-ad-refresh-design.md`

**Run DB tests with:** `AGF_TEST_DB_URL="postgresql://agf:agf@localhost:5433/agf_it" python -m pytest tests/discovery -q` — a SKIP is not a pass; confirm the summary shows passes, not "skipped".

---

## Reference (existing code this reuses)

- `repo.upsert_ad(conn, ar, advertiser_id, app_id)` — `ON CONFLICT (external_ad_id)` updates `is_active=EXCLUDED.is_active`, `end_date=COALESCE(...)`, `last_seen=now()`, inserts new. `external_ad_id` = `ar.ad_archive_id`.
- `search_ads(session, tokens, ...) -> (records, cursor, has_next)` (`extract/search.py:48`). `has_next` is True iff pagination stopped at `max_pages` with more pages available — the truncation signal. The facade's `search` currently **discards** it (`facade.py:73`).
- `pull_keeper_history` (`discovery/keeper_history.py:61`) — the per-advertiser fetch+upsert+series loop, selection = `keepers_missing_history`.
- Helpers in `keeper_history.py`: `_week_starts`, `_linked_app_id`; `reconstruct_weekly_ad_counts`/`new_ads_per_week` (`tracker/history.py`); `repo.ensure_text_creative`.
- `build_extractor(dry_run, now_epoch)` (`workers/main.py:107`).
- Test fake: `tests/discovery/conftest.py` `FakeExtractor` (`page_ads: dict`, `async search`). The keeper ExtractError test (`test_keeper_history.py:163`) subclasses it and overrides `search`.

---

## Task 1: `search_listing` — surface the truncation flag

**Files:**
- Modify: `src/agf/extract/facade.py` (add `search_listing` after `count_active_ads`, ~line 95)
- Modify: `tests/discovery/conftest.py` (`FakeExtractor`: add `page_truncated` field + `search_listing` delegating to `self.search`)
- Test: `tests/discovery/test_corpus_refresh.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/discovery/test_corpus_refresh.py`:
```python
"""Corpus ad-refresh: search_listing truncation flag, whole-corpus selection,
vanished-ad flip, and the budgeted refresh loop. CORPUSREF* sentinels."""
import asyncio
from tests.discovery.conftest import FakeExtractor, make_ad


def test_search_listing_returns_ads_and_truncation_flag():
    ads = [make_ad("cr-a1", "CORPUSREF1", "Adv", "https://x/app")]
    ext = FakeExtractor(page_ads={"CORPUSREF1": ads},
                        page_truncated={"CORPUSREF1": True})
    got, truncated = asyncio.run(ext.search_listing(page_id="CORPUSREF1", max_pages=12))
    assert [a.ad_archive_id for a in got] == ["cr-a1"]
    assert truncated is True
    # unknown page → empty, not truncated
    empty, trunc2 = asyncio.run(ext.search_listing(page_id="NOPE", max_pages=12))
    assert empty == [] and trunc2 is False
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/discovery/test_corpus_refresh.py::test_search_listing_returns_ads_and_truncation_flag -v`
Expected: FAIL — `FakeExtractor` has no `search_listing` / no `page_truncated`.

- [ ] **Step 3: Add `search_listing` to the test fake**

In `tests/discovery/conftest.py`, add a field to `FakeExtractor` (right after `page_ads`):
```python
    page_truncated: dict = field(default_factory=dict)   # view_all_page_id -> bool
```
And add this method (after `search`):
```python
    async def search_listing(self, *, page_id, countries=("US",), max_pages=12):
        # delegate to self.search so subclasses that override search (e.g. to raise
        # ExtractError) still drive search_listing; truncation comes from page_truncated.
        ads = await self.search(view_all_page_id=page_id, search_type="page",
                                active_status="ALL", max_pages=max_pages)
        return list(ads), bool(self.page_truncated.get(page_id, False))
```

- [ ] **Step 4: Add `search_listing` to the real facade**

In `src/agf/extract/facade.py`, after `count_active_ads` (ends ~line 95), add:
```python
    async def search_listing(self, *, page_id, countries=["US"],
                             max_pages=12) -> tuple[list, bool]:
        """An advertiser's full ALL-status ad listing + whether pagination was
        truncated at max_pages (more pages remained). Mirrors search() but returns the
        has_next flag search() discards, so the corpus refresh only reconciles is_active
        on a COMPLETE listing."""
        def attempt():
            ws = self._pool.acquire()
            records, _cursor, has_next = search_ads(
                ws.session, ws.tokens, query="", countries=countries,
                active_status="ALL", search_type="page", view_all_page_id=page_id,
                max_pages=max_pages, delay=self._search_delay,
                jitter=self._search_jitter, sleep=self._sleep)
            return records, bool(has_next)
        return await asyncio.to_thread(lambda: self._run_resilient(attempt))
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `python -m pytest tests/discovery/test_corpus_refresh.py::test_search_listing_returns_ads_and_truncation_flag -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/agf/extract/facade.py tests/discovery/conftest.py tests/discovery/test_corpus_refresh.py
git commit -m "feat(extract): search_listing surfaces pagination-exhaustion flag"
```

---

## Task 2: Extract `refresh_one_advertiser` (refactor, no behaviour change)

**Files:**
- Modify: `src/agf/discovery/keeper_history.py` (add `refresh_one_advertiser`; `pull_keeper_history` calls it)
- Test: `tests/discovery/test_keeper_history.py` (regression — existing tests must pass)

- [ ] **Step 1: Add `refresh_one_advertiser` above `pull_keeper_history`**

In `src/agf/discovery/keeper_history.py`, add this function (uses already-imported `text`, `date`, `repo`, `begin`, `reconstruct_weekly_ad_counts`, `new_ads_per_week`):
```python
async def refresh_one_advertiser(engine, extractor, advertiser_id, page_id, *,
                                 hist_pages: int = 12, weeks: int = 16,
                                 flip_vanished: bool = False) -> dict:
    """Re-scrape one advertiser's Meta ALL-listing and reconcile public.ads in one txn:
    upsert every ad (is_active/start_date/end_date/last_seen via repo.upsert_ad) + its
    text-creative backstop, then reconstruct the last `weeks` weekly metric rows. When
    flip_vanished AND the listing was NOT truncated, flip the advertiser's still-active
    DB ads that are absent from the listing to is_active=false (they ended-and-delisted).
    Raises ExtractError if the listing pull fails (caller counts it). Returns
    {ads_stored, truncated, flipped}."""
    ads, truncated = await extractor.search_listing(page_id=page_id, max_pages=hist_pages)
    seen, uniq = set(), []
    for ad in ads:
        if ad.ad_archive_id and ad.ad_archive_id not in seen:
            seen.add(ad.ad_archive_id)
            uniq.append(ad)
    weekly = reconstruct_weekly_ad_counts(uniq, weeks=weeks)
    new_weekly = new_ads_per_week(uniq, weeks=weeks)
    starts = _week_starts(date.today(), weeks)
    flipped = 0
    with begin(engine) as conn:
        app_id = _linked_app_id(conn, advertiser_id)
        for ad in uniq:
            ad_id = repo.upsert_ad(conn, ad, advertiser_id, app_id)
            repo.ensure_text_creative(conn, ad_id, ad)
        for wk_start, cnt, nnew in zip(starts, weekly, new_weekly):
            repo.upsert_app_advertiser_metric(conn, advertiser_id, wk_start,
                                              cnt, max(nnew, 1), None)
        if flip_vanished and not truncated:
            flipped = conn.execute(text("""
                UPDATE public.ads
                   SET is_active = false,
                       end_date  = COALESCE(end_date, last_seen)
                 WHERE advertiser_id = :adv AND is_active
                   AND external_ad_id <> ALL(:seen)
            """), {"adv": advertiser_id,
                   "seen": [str(s) for s in seen]}).rowcount
    return {"ads_stored": len(uniq), "truncated": truncated, "flipped": flipped}
```

- [ ] **Step 2: Rewrite `pull_keeper_history`'s loop to call it**

In `src/agf/discovery/keeper_history.py`, replace the body of `pull_keeper_history` (the `starts = ...` line and the whole `for advertiser_id, page_id in keepers:` loop, currently lines ~75-105) with:
```python
    stats = {"keepers_done": 0, "requests_spent": 0, "ads_stored": 0, "errors": 0}
    for advertiser_id, page_id in keepers:
        if stats["requests_spent"] + hist_pages > budget_requests:
            break
        stats["requests_spent"] += hist_pages
        try:
            res = await refresh_one_advertiser(engine, extractor, advertiser_id,
                                               page_id, hist_pages=hist_pages, weeks=weeks)
        except ExtractError:
            stats["errors"] += 1
            sleep(kw_delay)
            continue
        stats["keepers_done"] += 1
        stats["ads_stored"] += res["ads_stored"]
        sleep(kw_delay)
    return stats
```
(Leave the `with engine.connect()` / `keepers = keepers_missing_history(conn)` lines above it unchanged. The standalone `starts = _week_starts(...)` line that was before the loop is now computed inside `refresh_one_advertiser`, so delete it here.)

- [ ] **Step 3: Run the keeper-history regression suite**

Run: `AGF_TEST_DB_URL="postgresql://agf:agf@localhost:5433/agf_it" python -m pytest tests/discovery/test_keeper_history.py -v`
Expected: PASS (all). The ExtractError test still works because the `BoomExtractor` overrides `search`, and the fake's `search_listing` delegates to `self.search` (Task 1), so the raise still propagates through `refresh_one_advertiser`.

- [ ] **Step 4: Commit**

```bash
git add src/agf/discovery/keeper_history.py
git commit -m "refactor(discovery): extract refresh_one_advertiser from pull_keeper_history"
```

---

## Task 3: `advertisers_with_page` — whole-corpus selection

**Files:**
- Create: `src/agf/discovery/corpus_refresh.py`
- Test: `tests/discovery/test_corpus_refresh.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/discovery/test_corpus_refresh.py`:
```python
from sqlalchemy import text
from types import SimpleNamespace
from agf.db import repo
from agf.discovery.corpus_refresh import advertisers_with_page

CORPUS_PAGES = ("CORPUSREFA", "CORPUSREFB", "CORPUSREFC")


def _clean_corpus(conn):
    ids = conn.execute(text("SELECT id FROM public.advertisers WHERE page_id LIKE 'CORPUSREF%'")).scalars().all()
    if ids:
        conn.execute(text("DELETE FROM public.ads WHERE advertiser_id = ANY(:i)"), {"i": ids})
        conn.execute(text("DELETE FROM public.advertisers WHERE id = ANY(:i)"), {"i": ids})


def test_advertisers_with_page_stalest_first(pg_conn):
    _clean_corpus(pg_conn)
    # A: ad seen long ago; B: ad seen recently; C: no page_id (excluded)
    a = repo.upsert_advertiser(pg_conn, SimpleNamespace(page_id="CORPUSREFA", page_name="A", page_like_count=10))
    b = repo.upsert_advertiser(pg_conn, SimpleNamespace(page_id="CORPUSREFB", page_name="B", page_like_count=10))
    c = repo.upsert_advertiser(pg_conn, SimpleNamespace(page_id=None, page_name="C", page_like_count=10))
    repo.upsert_ad(pg_conn, make_ad("cr-old", "CORPUSREFA", "A", "https://x/app"), a, None)
    repo.upsert_ad(pg_conn, make_ad("cr-new", "CORPUSREFB", "B", "https://x/app"), b, None)
    pg_conn.execute(text("UPDATE public.ads SET last_seen = now() - interval '10 days' WHERE advertiser_id = :a"), {"a": a})
    pg_conn.execute(text("UPDATE public.ads SET last_seen = now() WHERE advertiser_id = :b"), {"b": b})

    pairs = advertisers_with_page(pg_conn)
    ours = [(aid, pid) for aid, pid in pairs if pid and pid.startswith("CORPUSREF")]
    assert (a, "CORPUSREFA") in ours and (b, "CORPUSREFB") in ours
    assert all(pid is not None for _, pid in ours)              # C (no page_id) excluded
    assert ours.index((a, "CORPUSREFA")) < ours.index((b, "CORPUSREFB"))  # stalest (A) first
```

- [ ] **Step 2: Run it to verify it fails**

Run: `AGF_TEST_DB_URL="postgresql://agf:agf@localhost:5433/agf_it" python -m pytest tests/discovery/test_corpus_refresh.py::test_advertisers_with_page_stalest_first -v`
Expected: FAIL — `agf.discovery.corpus_refresh` does not exist.

- [ ] **Step 3: Create `corpus_refresh.py` with the selection**

Create `src/agf/discovery/corpus_refresh.py`:
```python
"""Corpus-wide ad refresh: re-scrape EVERY advertiser's live Meta listing (not just
unscored keepers) so GemScore v3 running-days stays accurate. Reuses
keeper_history.refresh_one_advertiser per advertiser; budget-bounded + paced like the
keeper backfill. One-shot operator path (scripts/refresh_corpus_ads.py)."""
import time

from sqlalchemy import text

from agf.extract.errors import ExtractError
from agf.discovery.keeper_history import refresh_one_advertiser


def advertisers_with_page(conn) -> list[tuple[int, str]]:
    """(advertiser_id, page_id) for every advertiser with a non-null page_id, stalest
    first (their freshest ad's last_seen ascending; NULL = never scraped = first), so a
    budget-limited or interrupted run refreshes the most-stale advertisers first."""
    return [(r[0], r[1]) for r in conn.execute(text("""
        SELECT a.id, a.page_id
        FROM public.advertisers a
        LEFT JOIN public.ads ad ON ad.advertiser_id = a.id
        WHERE a.page_id IS NOT NULL
        GROUP BY a.id, a.page_id
        ORDER BY MAX(ad.last_seen) ASC NULLS FIRST, a.id
    """)).all()]
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `AGF_TEST_DB_URL="postgresql://agf:agf@localhost:5433/agf_it" python -m pytest tests/discovery/test_corpus_refresh.py::test_advertisers_with_page_stalest_first -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agf/discovery/corpus_refresh.py tests/discovery/test_corpus_refresh.py
git commit -m "feat(discovery): advertisers_with_page corpus selection (stalest first)"
```

---

## Task 4: The "trust the listing" vanished-ad flip

`refresh_one_advertiser` already has the `flip_vanished` parameter and flip SQL from Task 2. This task proves it with tests (TDD the behaviour, including the truncation guard).

**Files:**
- Test: `tests/discovery/test_corpus_refresh.py`
- (Implementation already in `keeper_history.py` from Task 2 — if a test fails, fix it there.)

- [ ] **Step 1: Write the failing tests**

Append to `tests/discovery/test_corpus_refresh.py`:
```python
from agf.discovery.keeper_history import refresh_one_advertiser


def test_flip_marks_vanished_ad_inactive_when_complete(pg_conn, _migrated_engine):
    _clean_corpus(pg_conn)
    eng = _migrated_engine
    adv = repo.upsert_advertiser(pg_conn, SimpleNamespace(page_id="CORPUSREFA", page_name="A", page_like_count=10))
    # two active ads in DB; fresh listing returns ONLY A1 (A2 vanished/ended)
    repo.upsert_ad(pg_conn, make_ad("cr-A1", "CORPUSREFA", "A", "https://x/app"), adv, None)
    repo.upsert_ad(pg_conn, make_ad("cr-A2", "CORPUSREFA", "A", "https://x/app"), adv, None)
    pg_conn.execute(text("UPDATE public.ads SET last_seen = now() - interval '5 days' WHERE advertiser_id = :a"), {"a": adv})
    pg_conn.commit()

    ext = FakeExtractor(page_ads={"CORPUSREFA": [make_ad("cr-A1", "CORPUSREFA", "A", "https://x/app")]},
                        page_truncated={"CORPUSREFA": False})
    res = asyncio.run(refresh_one_advertiser(eng, ext, adv, "CORPUSREFA",
                                             hist_pages=12, weeks=16, flip_vanished=True))
    assert res["flipped"] == 1
    rows = {r.external_ad_id: (r.is_active, r.last_seen) for r in pg_conn.execute(text(
        "SELECT external_ad_id, is_active, last_seen FROM public.ads WHERE advertiser_id = :a"), {"a": adv})}
    assert rows["cr-A1"][0] is True                 # still in listing → active, last_seen bumped
    assert rows["cr-A1"][1] is not None
    assert rows["cr-A2"][0] is False                # vanished → flipped inactive


def test_flip_skipped_when_listing_truncated(pg_conn, _migrated_engine):
    _clean_corpus(pg_conn)
    eng = _migrated_engine
    adv = repo.upsert_advertiser(pg_conn, SimpleNamespace(page_id="CORPUSREFB", page_name="B", page_like_count=10))
    repo.upsert_ad(pg_conn, make_ad("cr-B1", "CORPUSREFB", "B", "https://x/app"), adv, None)
    repo.upsert_ad(pg_conn, make_ad("cr-B2", "CORPUSREFB", "B", "https://x/app"), adv, None)
    pg_conn.commit()
    # listing returns only B1 BUT is truncated → cannot trust absence of B2
    ext = FakeExtractor(page_ads={"CORPUSREFB": [make_ad("cr-B1", "CORPUSREFB", "B", "https://x/app")]},
                        page_truncated={"CORPUSREFB": True})
    res = asyncio.run(refresh_one_advertiser(eng, ext, adv, "CORPUSREFB",
                                             hist_pages=12, weeks=16, flip_vanished=True))
    assert res["truncated"] is True and res["flipped"] == 0
    active = {r.external_ad_id: r.is_active for r in pg_conn.execute(text(
        "SELECT external_ad_id, is_active FROM public.ads WHERE advertiser_id = :a"), {"a": adv})}
    assert active["cr-B2"] is True                  # NOT flipped — listing was incomplete
```

Note: these tests use `_migrated_engine` (committing engine) because `refresh_one_advertiser` opens its own `begin(engine)` transactions; reads via `pg_conn` see them since both point at the same DB. The `_clean_corpus` + `pg_conn.commit()` make the seeds visible to the engine's separate connection. Mirror the keeper-history tests' commit/clean discipline; clean `CORPUSREF%` in a teardown if the suite leaves rows.

- [ ] **Step 2: Run them to verify behaviour**

Run: `AGF_TEST_DB_URL="postgresql://agf:agf@localhost:5433/agf_it" python -m pytest tests/discovery/test_corpus_refresh.py -k flip -v`
Expected: PASS (the flip logic landed in Task 2). If `test_flip_marks_vanished_ad_inactive_when_complete` fails on the empty-`seen` / array bind, verify the flip param binds `:seen` as a Postgres array (a Python list of str binds to `text[]`; `<> ALL('{}')` is vacuously true).

- [ ] **Step 3: Commit**

```bash
git add tests/discovery/test_corpus_refresh.py
git commit -m "test(discovery): vanished-ad flip + truncation guard"
```

---

## Task 5: `refresh_corpus` — the budgeted loop

**Files:**
- Modify: `src/agf/discovery/corpus_refresh.py` (add `refresh_corpus`)
- Test: `tests/discovery/test_corpus_refresh.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/discovery/test_corpus_refresh.py`:
```python
from agf.discovery.corpus_refresh import refresh_corpus


def test_refresh_corpus_refreshes_each_and_respects_budget(pg_conn, _migrated_engine):
    _clean_corpus(pg_conn)
    eng = _migrated_engine
    a = repo.upsert_advertiser(pg_conn, SimpleNamespace(page_id="CORPUSREFA", page_name="A", page_like_count=10))
    b = repo.upsert_advertiser(pg_conn, SimpleNamespace(page_id="CORPUSREFB", page_name="B", page_like_count=10))
    repo.upsert_ad(pg_conn, make_ad("cr-A1", "CORPUSREFA", "A", "https://x/app"), a, None)
    repo.upsert_ad(pg_conn, make_ad("cr-B1", "CORPUSREFB", "B", "https://x/app"), b, None)
    pg_conn.execute(text("UPDATE public.ads SET last_seen = now() - interval '9 days' WHERE advertiser_id = :a"), {"a": a})
    pg_conn.execute(text("UPDATE public.ads SET last_seen = now() - interval '1 days' WHERE advertiser_id = :b"), {"b": b})
    pg_conn.commit()

    ext = FakeExtractor(page_ads={
        "CORPUSREFA": [make_ad("cr-A1", "CORPUSREFA", "A", "https://x/app"),
                       make_ad("cr-A2", "CORPUSREFA", "A", "https://x/app", days_old=5)],
        "CORPUSREFB": [make_ad("cr-B1", "CORPUSREFB", "B", "https://x/app")],
    })
    # budget = one advertiser's pages → only the STALEST (A) is processed
    res = asyncio.run(refresh_corpus(eng, ext, hist_pages=12, budget_requests=12,
                                     sleep=lambda s: None, limit=None))
    assert res["advertisers_done"] == 1
    assert res["ads_stored"] == 2                   # A's two listing ads upserted
    a2 = pg_conn.execute(text("SELECT 1 FROM public.ads WHERE external_ad_id='cr-A2'")).first()
    assert a2 is not None                           # new ad A2 inserted for the stalest advertiser
```

- [ ] **Step 2: Run it to verify it fails**

Run: `AGF_TEST_DB_URL="postgresql://agf:agf@localhost:5433/agf_it" python -m pytest tests/discovery/test_corpus_refresh.py::test_refresh_corpus_refreshes_each_and_respects_budget -v`
Expected: FAIL — `refresh_corpus` not defined.

- [ ] **Step 3: Add `refresh_corpus`**

Append to `src/agf/discovery/corpus_refresh.py`:
```python
async def refresh_corpus(engine, extractor, *, hist_pages: int = 12,
                         budget_requests: int = 12_000, weeks: int = 16,
                         kw_delay: float = 2.0, sleep=time.sleep,
                         offset: int = 0, limit: int | None = None) -> dict:
    """Re-scrape the whole advertiser corpus (stalest first) under a proxy-request
    budget, flipping vanished ads on complete listings. The loop stops BEFORE an
    advertiser whose hist_pages pull would exceed budget_requests. offset/limit slice
    the target list (probe/resume). Returns
    {advertisers_done, requests_spent, ads_stored, flipped, truncated, errors}."""
    with engine.connect() as conn:
        targets = advertisers_with_page(conn)
    targets = targets[offset:]
    if limit is not None:
        targets = targets[:limit]
    stats = {"advertisers_done": 0, "requests_spent": 0, "ads_stored": 0,
             "flipped": 0, "truncated": 0, "errors": 0}
    for advertiser_id, page_id in targets:
        if stats["requests_spent"] + hist_pages > budget_requests:
            break
        stats["requests_spent"] += hist_pages
        try:
            res = await refresh_one_advertiser(engine, extractor, advertiser_id, page_id,
                                               hist_pages=hist_pages, weeks=weeks,
                                               flip_vanished=True)
        except ExtractError:
            stats["errors"] += 1
            sleep(kw_delay)
            continue
        stats["advertisers_done"] += 1
        stats["ads_stored"] += res["ads_stored"]
        stats["flipped"] += res["flipped"]
        stats["truncated"] += 1 if res["truncated"] else 0
        sleep(kw_delay)
    return stats
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `AGF_TEST_DB_URL="postgresql://agf:agf@localhost:5433/agf_it" python -m pytest tests/discovery/test_corpus_refresh.py -v`
Expected: PASS (all corpus_refresh tests).

- [ ] **Step 5: Commit**

```bash
git add src/agf/discovery/corpus_refresh.py tests/discovery/test_corpus_refresh.py
git commit -m "feat(discovery): refresh_corpus budgeted whole-corpus ad re-scrape"
```

---

## Task 6: Operator runner `scripts/refresh_corpus_ads.py`

**Files:**
- Create: `scripts/refresh_corpus_ads.py`

- [ ] **Step 1: Create the runner**

Create `scripts/refresh_corpus_ads.py`:
```python
"""Operator one-shot: re-scrape EVERY advertiser's live Meta listing to refresh
GemScore v3 running-days (is_active/start_date/end_date), reusing
discovery.corpus_refresh.refresh_corpus. Loads the live worker's production env (proxy
+ MinIO + localhost DB) from its launchd plist so it mirrors the daemon exactly.

PAUSE the worker first (it shares the proxy + holds no relevant mutex, but avoid double
proxy load):
    launchctl bootout gui/$(id -u)/com.fielddesk.worker

Then (from repo root):
    PYTHONPATH=src .venv/bin/python -m scripts.refresh_corpus_ads               # whole corpus
    PYTHONPATH=src .venv/bin/python -m scripts.refresh_corpus_ads --limit 10    # probe
    PYTHONPATH=src .venv/bin/python -m scripts.refresh_corpus_ads --offset 300  # resume

Restart the worker + rescore after:
    launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.fielddesk.worker.plist
    PYTHONPATH=src .venv/bin/python -c "from agf.db.engine import get_engine; \
      from agf.scoring.run_scoring import run_scoring; from datetime import date; \
      print('scored', run_scoring(get_engine(), date.today()))"
"""
import argparse
import asyncio
import os
import plistlib
import sys
import time

_PLIST = os.path.expanduser("~/Library/LaunchAgents/com.fielddesk.worker.plist")


def _load_plist_env() -> None:
    with open(_PLIST, "rb") as f:
        env = plistlib.load(f).get("EnvironmentVariables", {})
    for k, v in env.items():
        os.environ[k] = str(v)
    os.environ["AGF_DATABASE_URL"] = "postgresql://agf:agf@localhost:5433/agf"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--budget", type=int, default=12_000, help="max page requests")
    ap.add_argument("--hist-pages", type=int, default=12)
    args = ap.parse_args()

    _load_plist_env()
    from agf.db.engine import get_engine
    from agf.workers.main import build_extractor
    from agf.discovery.corpus_refresh import refresh_corpus

    now_epoch = int(time.time())
    extractor = build_extractor(False, now_epoch)
    stats = asyncio.run(refresh_corpus(
        get_engine(), extractor, hist_pages=args.hist_pages,
        budget_requests=args.budget, offset=args.offset, limit=args.limit))
    print(f"[refresh-corpus] {stats}", flush=True)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke-validate the runner imports + env load (no Meta calls)**

Run: `PYTHONPATH=src .venv/bin/python -c "import scripts.refresh_corpus_ads as m; m._load_plist_env(); print('env ok', bool(__import__('os').environ.get('AGF_PROXIES')))"`
Expected: prints `env ok True` (plist env loaded; no network).

- [ ] **Step 3: Commit**

```bash
git add scripts/refresh_corpus_ads.py
git commit -m "feat(scripts): refresh_corpus_ads operator runner"
```

- [ ] **Step 4: Full discovery suite green**

Run: `AGF_TEST_DB_URL="postgresql://agf:agf@localhost:5433/agf_it" python -m pytest tests/discovery -q`
Expected: PASS, none skipped.

---

## Task 7: Live run + rescore (operator, manual)

**Files:** none (operational).

- [ ] **Step 1: Probe 10 advertisers (validate real Meta path + measure rate)**

Pause the worker, then:
```bash
launchctl bootout gui/$(id -u)/com.fielddesk.worker
PYTHONPATH=src .venv/bin/python -m scripts.refresh_corpus_ads --limit 10
```
Expected: a stats line with `advertisers_done=10`, non-zero `ads_stored`, low `errors`. Spot-check in the DB that a probed advertiser's ads now have `last_seen` ≈ now and any stopped ad flipped `is_active=false`.

- [ ] **Step 2: Full corpus run (background)**

```bash
PYTHONPATH=src .venv/bin/python -m scripts.refresh_corpus_ads > /tmp/agf-corpus-refresh.log 2>&1 &
```
Monitor `/tmp/agf-corpus-refresh.log`; watch for `errors` climbing (proxy throttling → the facade backs off/rotates, but a high error rate means pause and retry the remaining via `--offset`).

- [ ] **Step 3: Restart worker + global rescore**

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.fielddesk.worker.plist
PYTHONPATH=src .venv/bin/python -c "from agf.db.engine import get_engine; from agf.scoring.run_scoring import run_scoring; from datetime import date; print('scored', run_scoring(get_engine(), date.today()))"
```
Expected: scoring completes; spot-check the feed — advertisers whose stale "active" ads flipped now have corrected winner_age/winner_depth.

---

## Self-review notes

- **Spec coverage:** refactor→`refresh_one_advertiser` (Task 2) ✓; corpus selection stalest-first (Task 3) ✓; truncation-guarded "trust the listing" flip (Task 2 impl + Task 4 tests) ✓; truncation signal via `search_listing`/`has_next` (Task 1) ✓; one-shot runner + ops sequence (Task 6/7) ✓; reuse `_linked_app_id` / no re-resolve ✓; no DB migration ✓; no worker-loop integration ✓.
- **Type consistency:** `refresh_one_advertiser(engine, extractor, advertiser_id, page_id, *, hist_pages, weeks, flip_vanished)` returns `{ads_stored, truncated, flipped}` — same signature in keeper_history (Task 2), tests (Task 4), and `refresh_corpus` (Task 5). `search_listing(*, page_id, countries, max_pages) -> (list, bool)` identical on facade + fake (Task 1).
- **Coordination:** writes `ads.is_active`/`end_date` that the parallel session's reconstruction reads — honoured its `end_date = last_seen` convention in the flip. New module `corpus_refresh.py` keeps `keeper_history.py` focused.
