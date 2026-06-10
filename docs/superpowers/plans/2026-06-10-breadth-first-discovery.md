# Breadth-First Ad-Library Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure ad-library discovery into a staged sweep → triage → enrich pipeline backed by a persistent `discovery.candidates` pool, so a daily US-only keyword sweep compounds instead of repeating, the keep/drop decision costs zero proxy requests, and deep enrichment runs only on keepers.

**Architecture:** Stage 1 (sweep) fans ~300 narrow keywords over Meta Ad Library SERPs (1–2 pages each, proxy), classifying candidates from data already on the result and persisting app-linked ones to a new `discovery.candidates` table. Stage 2 (triage) batch-resolves candidates against free Apple/Google store APIs and applies the user-set thresholds, promoting keepers into the existing `advertisers`/`apps` tracked pool. Stage 3 (enrich) is the existing deep pull, fed only by keepers under a request budget. A daily-gated worker step replaces the EU-firehose discovery path.

**Tech Stack:** Python 3.13, SQLAlchemy (text SQL), Postgres, curl_cffi via the existing `Extractor` facade, httpx for store APIs, pytest with the rolled-back `pg_conn` / session `_migrated_engine` fixtures (skip without `AGF_TEST_DB_URL`).

**Spec:** `docs/superpowers/specs/2026-06-10-breadth-first-discovery-design.md`

---

## File structure

| File | Action | Responsibility |
|---|---|---|
| `db/schema/112_discovery_candidates.sql` | Create | `discovery.candidates` pool + `discovery.sweep_runs` audit tables |
| `src/agf/db/candidates.py` | Create | All SQL for the two new tables (upsert/bump, fetch-new, mark-triaged, run bookkeeping) |
| `src/agf/discovery/taxonomy.py` | Modify | Expand to ~300 keywords; add `sweep_entries()` (app layers only, no funnel phrases) |
| `src/agf/discovery/sweep.py` | Create | Stage 1: SERP fold → inline drops → persist candidates |
| `src/agf/discovery/triage.py` | Create | Stage 2: pure keep/drop rules + store lookups + promotion |
| `src/agf/workers/main.py` | Modify | Daily-gated sweep→triage step replaces `run_discovery`; budget-bounded enrich feed |
| `src/agf/extract/fake.py` | Modify | `FakeExtractor.demo` serves keyword ads so dry-run exercises the sweep |
| `scripts/sweep_smoke.py` | Create | Live one-keyword e2e smoke (manual gate) |
| `ops/RUNBOOK.md` | Modify | Document the new discovery flow + env knobs |
| `tests/db/test_candidates.py` | Create | Pool dedup/status semantics |
| `tests/discovery/test_taxonomy.py` | Create | Taxonomy size/shape guarantees |
| `tests/discovery/test_sweep.py` | Create | Fold rules + sweep persistence |
| `tests/discovery/test_triage.py` | Create | Decision rules + promotion + lookup-failure handling |
| `tests/workers/test_main.py` | Modify | Dry-run asserts the new sweep→triage→enrich composition |

Conventions that apply to every task:
- Run tests with: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it PYTHONPATH=src .venv/bin/python -m pytest <path> -v` (DB tests skip without the env; **never** point at the `agf` database — the integration DB is `agf_it`).
- Run the verification step and READ its output BEFORE committing, as separate commands — never `pytest | tail && git commit` in one block.
- `git add` explicit paths only. Never `git add -A`.

---

### Task 1: Migration 112 — discovery schema

**Files:**
- Create: `db/schema/112_discovery_candidates.sql`

- [ ] **Step 1: Write the migration**

```sql
-- 112: breadth-first discovery pool (spec 2026-06-10-breadth-first-discovery-design)
-- discovery.candidates: every app-linked advertiser the sweep has ever seen, with a
-- triage status that never regresses. discovery.sweep_runs: one audit row per daily run.
CREATE SCHEMA IF NOT EXISTS discovery;

CREATE TABLE IF NOT EXISTS discovery.candidates (
  id                 BIGSERIAL PRIMARY KEY,
  page_id            TEXT NOT NULL,
  page_name          TEXT,
  page_like_count    INTEGER,
  store              TEXT NOT NULL CHECK (store IN ('ios','android')),
  store_id           TEXT NOT NULL,
  link_url           TEXT,
  cta_type           TEXT,
  ad_start_date      DATE,
  source_keyword     TEXT,
  technique          TEXT,
  times_seen         INTEGER NOT NULL DEFAULT 1,
  first_seen_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  status             TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new','kept','dropped')),
  drop_reason        TEXT,
  triaged_at         TIMESTAMPTZ,
  kept_advertiser_id BIGINT REFERENCES public.advertisers(id),
  UNIQUE (page_id, store, store_id)
);
CREATE INDEX IF NOT EXISTS candidates_status_idx ON discovery.candidates (status);

CREATE TABLE IF NOT EXISTS discovery.sweep_runs (
  id                 BIGSERIAL PRIMARY KEY,
  started_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at        TIMESTAMPTZ,
  keywords_swept     INTEGER NOT NULL DEFAULT 0,
  serp_requests      INTEGER NOT NULL DEFAULT 0,
  candidates_new     INTEGER NOT NULL DEFAULT 0,
  candidates_seen    INTEGER NOT NULL DEFAULT 0,
  triaged_kept       INTEGER NOT NULL DEFAULT 0,
  triaged_dropped    INTEGER NOT NULL DEFAULT 0,
  drop_reasons       JSONB,
  enrich_requests    INTEGER NOT NULL DEFAULT 0,
  enriched           INTEGER NOT NULL DEFAULT 0,
  error              TEXT
);
```

- [ ] **Step 2: Verify migrations stay idempotent**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it PYTHONPATH=src .venv/bin/python -m pytest tests/db/test_migrate.py -v`
Expected: PASS (the session fixture applies 112; second apply returns `[]`).

- [ ] **Step 3: Commit**

```bash
git add db/schema/112_discovery_candidates.sql
git commit -m "feat(db): discovery.candidates pool + sweep_runs audit (migration 112)"
```

---

### Task 2: Candidates repo

**Files:**
- Create: `src/agf/db/candidates.py`
- Test: `tests/db/test_candidates.py`

- [ ] **Step 1: Write the failing tests**

```python
"""discovery.candidates / sweep_runs repo semantics — dedup bumps, status never
regresses, daily-run gate. Rolled-back pg_conn; CANDTEST* sentinels only, and every
assertion filters to them (the shared agf_it DB may hold other suites' committed rows)."""
from datetime import date

from agf.db import candidates as cand

ROW = dict(page_id="CANDTEST1", page_name="Cand Test", page_like_count=900,
           store="ios", store_id="999111", link_url="https://apps.apple.com/us/app/x/id999111",
           cta_type="DOWNLOAD", ad_start_date=date(2026, 6, 1),
           source_keyword="pelvic floor app", technique="niche_term")


def _mine(rows):
    return [r for r in rows if r.page_id.startswith("CANDTEST")]


def test_upsert_inserts_then_bumps(pg_conn):
    cid, was_new = cand.upsert_sweep_candidate(pg_conn, ROW)
    assert was_new is True
    cid2, was_new2 = cand.upsert_sweep_candidate(
        pg_conn, {**ROW, "page_like_count": 1200, "source_keyword": "other kw"})
    assert cid2 == cid and was_new2 is False
    row = _mine(cand.new_candidates(pg_conn))[0]
    assert row.times_seen == 2
    assert row.page_like_count == 1200          # freshest non-null likes win
    assert row.source_keyword == "pelvic floor app"   # first-seen provenance kept


def test_dropped_stays_dropped_on_reseen(pg_conn):
    cid, _ = cand.upsert_sweep_candidate(pg_conn, ROW)
    cand.mark_triaged(pg_conn, cid, status="dropped", drop_reason="game")
    cand.upsert_sweep_candidate(pg_conn, ROW)            # swept again next day
    assert _mine(cand.new_candidates(pg_conn)) == []     # not re-triaged
    got = pg_conn.execute(
        __import__("sqlalchemy").text(
            "SELECT status, drop_reason, times_seen FROM discovery.candidates WHERE id=:i"),
        {"i": cid}).one()
    assert got.status == "dropped" and got.drop_reason == "game" and got.times_seen == 2


def test_mark_kept_records_advertiser(pg_conn):
    from agf.db.repo import upsert_advertiser
    from types import SimpleNamespace
    cid, _ = cand.upsert_sweep_candidate(pg_conn, ROW)
    adv = upsert_advertiser(pg_conn, SimpleNamespace(
        page_id="CANDTEST1", page_name="Cand Test", page_like_count=900))
    cand.mark_triaged(pg_conn, cid, status="kept", kept_advertiser_id=adv)
    assert adv in cand.kept_advertiser_ids(pg_conn)
    assert _mine(cand.new_candidates(pg_conn)) == []


def test_sweep_run_gate(pg_conn):
    today = date(2026, 6, 10)
    assert cand.sweep_ran_today(pg_conn, today) is False
    run_id = cand.open_sweep_run(pg_conn)
    assert cand.sweep_ran_today(pg_conn, today) is False   # unfinished run doesn't gate
    cand.close_sweep_run(pg_conn, run_id,
                         sweep={"keywords_swept": 3, "serp_requests": 6,
                                "candidates_new": 2, "candidates_seen": 1,
                                "drops": {"social": 4}},
                         triage={"kept": 1, "dropped": {"game": 1}})
    assert cand.sweep_ran_today(pg_conn, today) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it PYTHONPATH=src .venv/bin/python -m pytest tests/db/test_candidates.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agf.db.candidates'`

- [ ] **Step 3: Implement `src/agf/db/candidates.py`**

```python
"""Repo for the breadth-first discovery pool (spec 2026-06-10).

discovery.candidates is append-mostly: a re-seen candidate bumps last_seen_at /
times_seen / page_like_count but NEVER regresses status — a dropped candidate is
never re-triaged. conn-first, no commits (callers own the transaction)."""
import json
from sqlalchemy import text


def upsert_sweep_candidate(conn, row: dict) -> tuple[int, bool]:
    """Insert or bump one swept candidate. Returns (id, was_new)."""
    got = conn.execute(text("""
        INSERT INTO discovery.candidates
          (page_id, page_name, page_like_count, store, store_id, link_url,
           cta_type, ad_start_date, source_keyword, technique)
        VALUES (:page_id, :page_name, :page_like_count, :store, :store_id, :link_url,
                :cta_type, :ad_start_date, :source_keyword, :technique)
        ON CONFLICT (page_id, store, store_id) DO UPDATE SET
          times_seen      = discovery.candidates.times_seen + 1,
          last_seen_at    = now(),
          page_like_count = COALESCE(EXCLUDED.page_like_count,
                                     discovery.candidates.page_like_count)
        RETURNING id, (xmax = 0) AS was_new
    """), row).one()
    return got.id, bool(got.was_new)


def new_candidates(conn, limit: int | None = None):
    """Untriaged candidates, oldest first."""
    q = ("SELECT id, page_id, page_name, page_like_count, store, store_id, "
         "times_seen, source_keyword FROM discovery.candidates "
         "WHERE status = 'new' ORDER BY first_seen_at, id")
    if limit:
        q += f" LIMIT {int(limit)}"
    return conn.execute(text(q)).all()


def mark_triaged(conn, candidate_id: int, *, status: str,
                 drop_reason: str | None = None,
                 kept_advertiser_id: int | None = None) -> None:
    conn.execute(text(
        "UPDATE discovery.candidates SET status=:s, drop_reason=:r, "
        "kept_advertiser_id=:a, triaged_at=now() WHERE id=:i"),
        {"s": status, "r": drop_reason, "a": kept_advertiser_id, "i": candidate_id})


def kept_advertiser_ids(conn) -> list[int]:
    """Advertiser ids of kept candidates, oldest-triaged first (the enrich feed)."""
    return [r[0] for r in conn.execute(text(
        "SELECT DISTINCT ON (kept_advertiser_id) kept_advertiser_id "
        "FROM discovery.candidates WHERE status='kept' AND kept_advertiser_id IS NOT NULL "
        "ORDER BY kept_advertiser_id, triaged_at")).all()]


def sweep_ran_today(conn, as_of) -> bool:
    """True when a sweep run FINISHED on `as_of` (a crashed run doesn't block the day)."""
    return bool(conn.execute(text(
        "SELECT EXISTS(SELECT 1 FROM discovery.sweep_runs "
        "WHERE started_at::date = :d AND finished_at IS NOT NULL)"),
        {"d": as_of}).scalar())


def open_sweep_run(conn) -> int:
    return conn.execute(text(
        "INSERT INTO discovery.sweep_runs DEFAULT VALUES RETURNING id")).scalar_one()


def close_sweep_run(conn, run_id: int, *, sweep: dict | None = None,
                    triage: dict | None = None, error: str | None = None) -> None:
    sweep = sweep or {}
    triage = triage or {}
    reasons = {**(sweep.get("drops") or {}), **(triage.get("dropped") or {})}
    conn.execute(text("""
        UPDATE discovery.sweep_runs SET finished_at = now(),
          keywords_swept  = :kw, serp_requests = :req,
          candidates_new  = :new, candidates_seen = :seen,
          triaged_kept    = :kept, triaged_dropped = :dropped,
          drop_reasons    = CAST(:reasons AS jsonb), error = :err
        WHERE id = :id
    """), {"kw": sweep.get("keywords_swept", 0), "req": sweep.get("serp_requests", 0),
           "new": sweep.get("candidates_new", 0), "seen": sweep.get("candidates_seen", 0),
           "kept": triage.get("kept", 0),
           "dropped": sum((triage.get("dropped") or {}).values()),
           "reasons": json.dumps(reasons), "err": error, "id": run_id})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it PYTHONPATH=src .venv/bin/python -m pytest tests/db/test_candidates.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add src/agf/db/candidates.py tests/db/test_candidates.py
git commit -m "feat(discovery): candidates-pool repo — dedup bumps, status never regresses"
```

---

### Task 3: Taxonomy expansion + `sweep_entries()`

**Files:**
- Modify: `src/agf/discovery/taxonomy.py`
- Test: `tests/discovery/test_taxonomy.py`

- [ ] **Step 1: Write the failing test**

```python
"""Sweep taxonomy guarantees: app-focused breadth, no funnel-phrase layer."""
from agf.discovery import taxonomy


def test_sweep_entries_exclude_funnel_layer():
    layers = {e.layer for e in taxonomy.sweep_entries()}
    assert layers == {"niche_term", "store_fragment"}


def test_sweep_taxonomy_is_wide():
    entries = taxonomy.sweep_entries()
    assert len(entries) >= 250                       # spec: ~250-350 keywords
    assert len({e.keyword for e in entries}) == len(entries)   # no duplicates


def test_all_entries_still_serves_experiment_harness():
    assert len(taxonomy.all_entries(layer="funnel_phrase")) >= 28
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/discovery/test_taxonomy.py -v`
Expected: FAIL — `AttributeError: module 'agf.discovery.taxonomy' has no attribute 'sweep_entries'`

- [ ] **Step 3: Expand the taxonomy**

In `src/agf/discovery/taxonomy.py`, replace the `NICHE_TERMS` list with the grouped lists below (keeps the original 28 terms inside `_NICHE_CORE`), extend `STORE_FRAGMENTS`, and add `sweep_entries()`. The docstring's "Three layers" note gains one line: "The daily production sweep uses `sweep_entries()` (niche+store only — funnel phrases find funnels, which are v2)."

```python
# Layer 2 — app-niche / pain-point terms (non-game leaning). Grouped by vertical so
# additions stay reviewable; the probes showed NARROW keywords surface small apps.
_NICHE_CORE = [
    "calorie tracker app", "habit tracker app", "sleep sounds app", "meditation app",
    "anxiety relief app", "language learning app", "journaling app", "workout plan app",
    "intermittent fasting app", "cycle tracking app", "budgeting app", "sobriety app",
    "posture corrector app", "brain training app", "breathing exercise app", "water reminder app",
    "gratitude journal app", "focus timer app", "weight loss app", "skin care routine app",
    "plant identifier app", "AI tutor app", "manifestation app", "couples relationship app",
    "pelvic floor app", "menopause app", "pilates app", "running coach app",
]
_NICHE_FITNESS = [
    "wall pilates app", "kettlebell workout app", "calisthenics app", "chair yoga app",
    "stretching routine app", "mobility routine app", "somatic exercises app",
    "home workout no equipment app", "30 day fitness challenge app", "couch to 5k app",
    "marathon training app", "trail running app", "jump rope workout app",
    "boxing workout app", "resistance band workout app", "glute workout app",
    "abs workout app", "posture exercises app", "back pain relief app",
    "knee pain exercises app", "physical therapy exercises app", "foam rolling app",
    "walking for weight loss app", "step challenge app", "rucking app",
    "zone 2 training app", "heart rate training app", "swim workout app",
    "rowing workout app", "climbing training app", "golf swing analyzer app",
    "tennis training app", "pickleball app", "balance exercises seniors app",
    "workout for seniors app", "cold plunge app", "breathwork app",
]
_NICHE_HEALTH_WOMEN = [
    "perimenopause app", "hormone balance app", "pcos app", "fertility tracker app",
    "pregnancy workout app", "postpartum recovery app", "breastfeeding tracker app",
    "baby sleep app", "baby feeding tracker app", "contraction timer app",
    "face yoga app", "hair growth app", "skincare routine builder app", "skin analyzer app",
]
_NICHE_DIET = [
    "macro tracker app", "carnivore diet app", "keto diet app", "mediterranean diet app",
    "anti inflammatory diet app", "gluten free scanner app", "food intolerance app",
    "meal prep planner app", "grocery list app", "recipe organizer app", "ai recipe app",
    "calorie photo scanner app", "glucose tracker app", "gut health app",
    "protein tracker app", "supplement tracker app", "fasting tracker app",
    "weight loss for women app", "emotional eating app", "binge eating recovery app",
    "mindful eating app", "fiber tracker app",
]
_NICHE_SLEEP = [
    "sleep tracker app", "snore recording app", "white noise baby app", "brown noise app",
    "sleep stories app", "lucid dreaming app", "dream journal app", "smart alarm app",
    "insomnia therapy app", "shift work sleep app",
]
_NICHE_MENTAL = [
    "cbt therapy app", "dbt skills app", "adhd planner app", "adhd focus app",
    "ocd app", "panic attack relief app", "mood tracker app", "self esteem app",
    "shadow work app", "inner child healing app", "grief support app",
    "social anxiety app", "overthinking app", "ai therapist app", "affirmations app",
    "hypnosis app", "eft tapping app", "vagus nerve exercises app",
    "nervous system regulation app", "breathing for anxiety app", "stress relief games app",
    "quit smoking app", "quit vaping app", "quit drinking app", "nofap app",
    "porn addiction recovery app", "adhd body doubling app",
]
_NICHE_PRODUCTIVITY = [
    "habit streak app", "dopamine detox app", "screen time blocker app",
    "app blocker for focus", "phone addiction app", "digital detox app",
    "pomodoro timer app", "deep work app", "adhd task manager app", "daily planner app",
    "second brain app", "ai notes app", "voice notes transcription app",
    "meeting notes ai app", "goal tracker app", "morning routine app", "5am club app",
    "journaling prompts app", "gratitude practice app", "stoic journal app",
    "bullet journal app", "accountability partner app", "cleaning schedule app",
    "declutter app", "home inventory app",
]
_NICHE_LEARNING = [
    "learn spanish app", "learn japanese app", "learn korean app", "learn french app",
    "language immersion app", "vocabulary builder app", "ielts prep app", "sat prep app",
    "mcat prep app", "nclex prep app", "spaced repetition app", "speed reading app",
    "book summaries app", "reading tracker app", "learn to code app", "learn python app",
    "chess training app", "music theory app", "learn piano app", "learn guitar app",
    "ear training app", "drawing lessons app", "learn to sing app", "public speaking app",
    "memory training app", "trivia learning app", "history learning app", "philosophy app",
    "homework scanner app", "ai math solver app", "kids learning games app",
    "toddler learning app", "kids bedtime stories app", "brain games for seniors app",
]
_NICHE_AI_TOOLS = [
    "ai headshot app", "ai photo enhancer app", "ai art generator app",
    "ai video editor app", "ai voice changer app", "ai companion app",
    "ai resume builder app", "ai interview prep app", "ai essay writer app",
    "ai language tutor app", "ai fitness coach app", "ai nutrition coach app",
    "ai stylist app", "ai interior design app", "ai tattoo generator app",
    "ai baby face generator app", "ai dream interpreter app", "ai girlfriend app",
]
_NICHE_FINANCE = [
    "budget planner app", "zero based budget app", "envelope budgeting app",
    "couples expense tracker app", "debt payoff app", "credit score builder app",
    "save money challenge app", "round up savings app", "investing for beginners app",
    "paper trading app", "dividend tracker app", "crypto portfolio app",
    "net worth tracker app", "bill reminder app", "subscription tracker app",
    "side hustle app", "freelance invoice app", "receipt scanner app",
]
_NICHE_FAMILY_RELATION = [
    "co parenting app", "family organizer app", "kids chore chart app",
    "potty training app", "teen allowance app", "baby milestones app",
    "due date countdown app", "couples quiz app", "long distance relationship app",
    "date night ideas app", "marriage counseling app", "love language app",
    "attachment style app", "breakup recovery app", "conversation starters app",
]
_NICHE_SPIRITUAL = [
    "bible study app", "daily devotional app", "prayer journal app",
    "christian meditation app", "quran memorization app", "islamic prayer times app",
    "catholic rosary app", "tarot reading app", "astrology birth chart app",
    "human design app", "numerology app", "moon phases app",
    "manifestation journal app", "vision board app",
]
_NICHE_LIFESTYLE = [
    "plant care reminder app", "mushroom identifier app", "bird identifier app",
    "rock identifier app", "coin identifier app", "antique appraisal app",
    "wine scanner app", "outfit planner app", "capsule wardrobe app",
    "car maintenance app", "fuel tracker app", "road trip planner app",
    "travel itinerary app", "packing list app", "flight deals app",
    "fishing forecast app", "golf gps app", "disc golf app",
    "knitting counter app", "crochet patterns app", "woodworking plans app",
    "guitar tuner app", "karaoke app", "audiobook player app",
    "widget maker app", "watch faces app", "live wallpaper app",
    "hearing test app", "eye exercises app", "interior design planner app",
]
NICHE_TERMS = (_NICHE_CORE + _NICHE_FITNESS + _NICHE_HEALTH_WOMEN + _NICHE_DIET
               + _NICHE_SLEEP + _NICHE_MENTAL + _NICHE_PRODUCTIVITY + _NICHE_LEARNING
               + _NICHE_AI_TOOLS + _NICHE_FINANCE + _NICHE_FAMILY_RELATION
               + _NICHE_SPIRITUAL + _NICHE_LIFESTYLE)

# Layer 3 — store-URL / install-CTA fragments (broad app-install baseline)
STORE_FRAGMENTS = [
    "apps.apple.com", "play.google.com", "download on the app store",
    "get it on google play", "download the app", "available on ios", "available on android",
    "download our app", "install the app", "try the app free", "now on the app store",
    "download for free", "our new app",
]
```

And append at the bottom of the file:

```python
def sweep_entries() -> list[TaxonomyEntry]:
    """The daily production sweep: app-focused layers only. Funnel phrases find
    funnels (v2) — their proxy budget goes to app keywords instead."""
    return all_entries(layer="niche_term") + all_entries(layer="store_fragment")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/discovery/test_taxonomy.py -v`
Expected: 3 PASS (NICHE_TERMS ≈ 245 + 13 fragments ⇒ `sweep_entries()` ≈ 258)

- [ ] **Step 5: Commit**

```bash
git add src/agf/discovery/taxonomy.py tests/discovery/test_taxonomy.py
git commit -m "feat(discovery): ~260-keyword app-focused sweep taxonomy + sweep_entries()"
```

---

### Task 4: Sweep module

**Files:**
- Create: `src/agf/discovery/sweep.py`
- Test: `tests/discovery/test_sweep.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Stage-1 sweep: SERP ad -> candidate fold rules, and persistence via FakeExtractor.
DB tests use SWEEPTEST* sentinels on the rolled-back pg_conn-style engine txn."""
import asyncio
from datetime import date

from sqlalchemy import text

from agf.discovery.sweep import classify_candidate, run_sweep
from agf.discovery.taxonomy import TaxonomyEntry
from agf.parse import AdRecord
from tests.discovery.conftest import FakeExtractor

NOW = 1_749_000_000


def ad(link, likes=500, page_id="SWEEPTEST1", cta="DOWNLOAD"):
    return AdRecord("a1", page_id, "Sweep Test", True, NOW - 5 * 86_400, None, 1,
                    cta, "Install", link, "body text", page_like_count=likes)


# --- fold rules --------------------------------------------------------------
def test_ios_store_link_is_a_candidate():
    disposition, row = classify_candidate(ad("https://apps.apple.com/us/app/x/id12345"),
                                          keyword="pelvic floor app", technique="niche_term")
    assert disposition == "candidate"
    assert row["store"] == "ios" and row["store_id"] == "12345"
    assert row["page_id"] == "SWEEPTEST1" and row["page_like_count"] == 500
    assert row["source_keyword"] == "pelvic floor app"
    assert row["ad_start_date"] is not None


def test_android_store_link_is_a_candidate():
    d, row = classify_candidate(ad("https://play.google.com/store/apps/details?id=com.x.y"),
                                keyword="k", technique="t")
    assert d == "candidate" and row["store"] == "android" and row["store_id"] == "com.x.y"


def test_drops():
    cases = [
        (ad("https://example.com/landing"), "non_app"),          # web/funnel -> v2
        (ad("https://www.instagram.com/someone"), "social"),
        (ad("https://apps.apple.com/us/app/x/id12345", likes=10_001), "incumbent_likes"),
        (ad("https://apps.apple.com/us/app/broken"), "no_store_id"),
        (ad(None), "no_link"),
    ]
    for record, expected in cases:
        disposition, row = classify_candidate(record, keyword="k", technique="t")
        assert (disposition, row) == (expected, None)


def test_threshold_boundary_is_inclusive():
    d, _ = classify_candidate(ad("https://apps.apple.com/us/app/x/id1", likes=10_000),
                              keyword="k", technique="t")
    assert d == "candidate"                       # exactly 10k passes (veto is >, not >=)


def test_null_likes_pass():                       # NULL/unknown never vetoes (repo rule)
    d, _ = classify_candidate(ad("https://apps.apple.com/us/app/x/id1", likes=None),
                              keyword="k", technique="t")
    assert d == "candidate"


# --- persistence -------------------------------------------------------------
def test_run_sweep_persists_and_counts(_migrated_engine):
    eng = _migrated_engine
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM discovery.candidates WHERE page_id LIKE 'SWEEPTEST%'"))
    try:
        ext = FakeExtractor(keyword_ads={
            "pelvic floor app": [
                ad("https://apps.apple.com/us/app/x/id12345"),
                ad("https://www.instagram.com/x", page_id="SWEEPTEST2"),
            ],
            "menopause app": [
                ad("https://apps.apple.com/us/app/x/id12345"),        # dup -> bump
            ],
        })
        entries = [TaxonomyEntry("pelvic floor app", "niche_term", "KEYWORD_UNORDERED", 2),
                   TaxonomyEntry("menopause app", "niche_term", "KEYWORD_UNORDERED", 2)]
        stats = asyncio.run(run_sweep(eng, ext, entries=entries, sleep=lambda s: None))
        assert stats["keywords_swept"] == 2
        assert stats["candidates_new"] == 1
        assert stats["candidates_seen"] == 1            # the dup bumped
        assert stats["drops"]["social"] == 1
        with eng.connect() as conn:
            row = conn.execute(text(
                "SELECT times_seen, status FROM discovery.candidates "
                "WHERE page_id='SWEEPTEST1' AND store_id='12345'")).one()
        assert row.times_seen == 2 and row.status == "new"
    finally:
        with eng.begin() as conn:
            conn.execute(text("DELETE FROM discovery.candidates WHERE page_id LIKE 'SWEEPTEST%'"))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it PYTHONPATH=src .venv/bin/python -m pytest tests/discovery/test_sweep.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agf.discovery.sweep'`

- [ ] **Step 3: Implement `src/agf/discovery/sweep.py`**

```python
"""Stage 1 of breadth-first discovery (spec 2026-06-10): a wide, shallow, US-only
keyword sweep that persists app-linked candidates using ONLY fields already present
on the SERP result — zero requests beyond the search pages themselves.

Inline drops (cheapest-first): no link -> social host -> incumbent page likes
(INCUMBENT_PAGE_LIKES_MAX, the user-set 10k constant — single source of truth in
scoring.vetoes) -> non-app destination (funnels are v2) -> store link without an id.

Commits per keyword (short transactions), so a mid-run throttle-kill keeps progress."""
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

from agf.classify import classify_url
from agf.db.candidates import upsert_sweep_candidate
from agf.db.engine import begin
from agf.discovery.taxonomy import sweep_entries
from agf.extract.errors import ExtractError
from agf.scoring.vetoes import INCUMBENT_PAGE_LIKES_MAX

# Destinations that are neither an app nor a funnel — a social page or ad redirect.
SOCIAL_HOSTS = {"facebook.com", "fb.me", "m.facebook.com", "l.facebook.com", "lm.facebook.com",
                "instagram.com", "api.whatsapp.com", "wa.me", "t.co", "ad.doubleclick.net",
                "youtube.com", "youtu.be", "twitter.com", "x.com", "linktr.ee", "messenger.com"}


def _host(url: str | None) -> str:
    return urlparse(url).netloc.lower().replace("www.", "") if url else ""


def _start_date(epoch) -> object | None:
    if not epoch:
        return None
    try:
        return datetime.fromtimestamp(float(epoch), tz=timezone.utc).date()
    except (TypeError, ValueError, OSError):
        return None


def classify_candidate(ad, *, keyword: str, technique: str) -> tuple[str, dict | None]:
    """Fold one AdRecord into (disposition, candidate-row). Dispositions:
    'candidate' | 'no_link' | 'social' | 'incumbent_likes' | 'non_app' | 'no_store_id'."""
    if not ad.page_id or not ad.link_url:
        return "no_link", None
    if _host(ad.link_url) in SOCIAL_HOSTS:
        return "social", None
    likes = ad.page_like_count
    if likes is not None and likes > INCUMBENT_PAGE_LIKES_MAX:
        return "incumbent_likes", None
    intent = classify_url(ad.link_url)
    if intent.kind not in ("ios", "android"):
        return "non_app", None
    if not intent.store_id:
        return "no_store_id", None
    return "candidate", {
        "page_id": ad.page_id, "page_name": ad.page_name, "page_like_count": likes,
        "store": intent.kind, "store_id": str(intent.store_id), "link_url": ad.link_url,
        "cta_type": ad.cta_type, "ad_start_date": _start_date(ad.start_date),
        "source_keyword": keyword, "technique": technique,
    }


async def run_sweep(engine, extractor, *, entries=None, keywords=None,
                    countries=("US",), max_pages: int = 2, kw_delay: float = 3.0,
                    sleep=time.sleep) -> dict:
    """Sweep the taxonomy (or an explicit entry/keyword subset) and persist candidates.
    Returns counters for sweep_runs. `keywords` (list[str]) is a convenience override
    used by the worker dry-run; it implies KEYWORD_UNORDERED."""
    if entries is None:
        entries = sweep_entries()
    if keywords is not None:
        entries = [e for e in entries if e.keyword in set(keywords)]
    stats = {"keywords_swept": 0, "serp_requests": 0, "raw_ads": 0,
             "candidates_new": 0, "candidates_seen": 0, "errors": 0,
             "drops": {"no_link": 0, "social": 0, "incumbent_likes": 0,
                       "non_app": 0, "no_store_id": 0}}
    for entry in entries:
        try:
            ads = await extractor.search(query=entry.keyword, countries=list(countries),
                                         search_type=entry.search_type, max_pages=max_pages)
        except ExtractError:
            stats["errors"] += 1
            sleep(kw_delay * 2)
            continue
        stats["keywords_swept"] += 1
        stats["serp_requests"] += max_pages          # upper bound; search may stop earlier
        stats["raw_ads"] += len(ads)
        rows, seen_keys = [], set()
        for ad in ads:
            disposition, row = classify_candidate(ad, keyword=entry.keyword,
                                                  technique=entry.layer)
            if disposition != "candidate":
                stats["drops"][disposition] += 1
                continue
            key = (row["page_id"], row["store"], row["store_id"])
            if key in seen_keys:                      # same candidate, several ads, one SERP
                continue
            seen_keys.add(key)
            rows.append(row)
        if rows:                                      # one short txn per keyword
            with begin(engine) as conn:
                for row in rows:
                    _, was_new = upsert_sweep_candidate(conn, row)
                    stats["candidates_new" if was_new else "candidates_seen"] += 1
        sleep(kw_delay)
    return stats
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it PYTHONPATH=src .venv/bin/python -m pytest tests/discovery/test_sweep.py -v`
Expected: 7 PASS

- [ ] **Step 5: Commit**

```bash
git add src/agf/discovery/sweep.py tests/discovery/test_sweep.py
git commit -m "feat(discovery): stage-1 sweep — SERP-only candidate fold + per-keyword persistence"
```

---

### Task 5: Triage module

**Files:**
- Create: `src/agf/discovery/triage.py`
- Test: `tests/discovery/test_triage.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Stage-2 triage: pure decision rules, lookup-failure handling, and promotion of
keepers into advertisers/apps. TRIAGETEST* sentinels; pg_conn rolls back."""
from agf.discovery.triage import triage_decision, run_triage
from agf.db import candidates as cand
from agf.enrich import AppMeta
from sqlalchemy import text


def meta(**kw):
    base = dict(track_id="111", name="App", seller="Dev", genre="Health & Fitness",
                release_date="2026-05-01", rating_count=100, avg_rating=4.5,
                installs=None, is_game=False)
    return AppMeta(**{**base, **kw})


# --- decision rules ----------------------------------------------------------
def test_decision_rules():
    assert triage_decision(None) == ("dropped", "no_us_listing")
    assert triage_decision(meta(is_game=True)) == ("dropped", "game")
    assert triage_decision(meta(installs=100_001)) == ("dropped", "installs_over_threshold")
    assert triage_decision(meta(rating_count=5_001)) == ("dropped", "ratings_over_threshold")
    assert triage_decision(meta(installs=100_000, rating_count=5_000)) == ("kept", None)
    assert triage_decision(meta(is_game=None)) == ("kept", None)   # unknown passes (repo rule)


# --- orchestration -----------------------------------------------------------
def _seed(pg_conn, store="ios", store_id="881100", page_id="TRIAGETEST1"):
    # run_triage processes EVERY status='new' row, so park any committed leftovers
    # from other suites first (rolled back with the txn — shared-DB isolation lesson).
    pg_conn.execute(text(
        "UPDATE discovery.candidates SET status='dropped', drop_reason='test_isolation' "
        "WHERE status='new' AND page_id NOT LIKE 'TRIAGETEST%'"))
    cid, _ = cand.upsert_sweep_candidate(pg_conn, dict(
        page_id=page_id, page_name="Triage Test", page_like_count=800,
        store=store, store_id=store_id, link_url="https://x", cta_type=None,
        ad_start_date=None, source_keyword="k", technique="t"))
    return cid


def test_keeper_is_promoted(pg_conn):
    cid = _seed(pg_conn)
    stats = run_triage(pg_conn,
                       lookup_ios=lambda ids: {"881100": meta(track_id="881100")},
                       lookup_android=lambda ids: {})
    assert stats["kept"] == 1 and stats["dropped"] == {}
    row = pg_conn.execute(text(
        "SELECT status, kept_advertiser_id FROM discovery.candidates WHERE id=:i"),
        {"i": cid}).one()
    assert row.status == "kept" and row.kept_advertiser_id is not None
    adv = pg_conn.execute(text(
        "SELECT page_name, page_like_count FROM public.advertisers WHERE id=:i"),
        {"i": row.kept_advertiser_id}).one()
    assert adv.page_name == "Triage Test" and adv.page_like_count == 800
    app = pg_conn.execute(text(
        "SELECT name, is_game, store FROM public.apps WHERE store='ios' AND store_id='881100'")).one()
    assert app.name == "App" and app.is_game is False


def test_android_keeper_gets_android_store(pg_conn):
    _seed(pg_conn, store="android", store_id="com.triage.test", page_id="TRIAGETEST2")
    run_triage(pg_conn, lookup_ios=lambda ids: {},
               lookup_android=lambda ids: {"com.triage.test": meta(track_id="com.triage.test",
                                                                   installs=4_000)})
    app = pg_conn.execute(text(
        "SELECT store, installs FROM public.apps WHERE store_id='com.triage.test'")).one()
    assert app.store == "android" and app.installs == 4_000


def test_missing_listing_drops(pg_conn):
    cid = _seed(pg_conn, store_id="000000", page_id="TRIAGETEST3")
    stats = run_triage(pg_conn, lookup_ios=lambda ids: {}, lookup_android=lambda ids: {})
    assert stats["dropped"] == {"no_us_listing": 1}
    status = pg_conn.execute(text(
        "SELECT status FROM discovery.candidates WHERE id=:i"), {"i": cid}).scalar()
    assert status == "dropped"


def test_lookup_exception_leaves_candidates_new(pg_conn):
    cid = _seed(pg_conn, page_id="TRIAGETEST4")

    def boom(ids):
        raise OSError("itunes down")
    stats = run_triage(pg_conn, lookup_ios=boom, lookup_android=lambda ids: {})
    assert stats["lookup_failed_ios"] is True and stats["kept"] == 0
    status = pg_conn.execute(text(
        "SELECT status FROM discovery.candidates WHERE id=:i"), {"i": cid}).scalar()
    assert status == "new"                      # retried next run


def test_gplay_sidecar_crash_leaves_android_new(pg_conn):
    cid = _seed(pg_conn, store="android", store_id="com.x.crash", page_id="TRIAGETEST5")
    # lookup_google_play returns {} when the sidecar crashes — with android ids pending,
    # an empty dict is indistinguishable from "all delisted", so treat it as a failure.
    stats = run_triage(pg_conn, lookup_ios=lambda ids: {}, lookup_android=lambda ids: {})
    assert stats["lookup_failed_android"] is True
    status = pg_conn.execute(text(
        "SELECT status FROM discovery.candidates WHERE id=:i"), {"i": cid}).scalar()
    assert status == "new"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it PYTHONPATH=src .venv/bin/python -m pytest tests/discovery/test_triage.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agf.discovery.triage'`

- [ ] **Step 3: Implement `src/agf/discovery/triage.py`**

```python
"""Stage 2 of breadth-first discovery (spec 2026-06-10): the free keep/drop gate.

Decision inputs come ONLY from store lookups (iTunes batch / Play sidecar — both
direct, zero proxy) plus what the sweep already stored. Thresholds are imported
from scoring.vetoes (the user-set constants) — never redefined here. release_date
is recorded on the app row but is NOT a gate (an older app that just started
scaling ads must still get in).

Failure semantics: a lookup that raises (or a Play sidecar crash, which surfaces
as an empty dict while ids were pending) leaves its candidates at status 'new' so
tomorrow's run retries them; an id absent from a SUCCESSFUL lookup is a dead/
missing US listing -> dropped."""
from types import SimpleNamespace

from agf.db import repo
from agf.db.candidates import mark_triaged, new_candidates
from agf.scoring.vetoes import ESTABLISHED_INSTALLS_MAX, ESTABLISHED_RATINGS_MAX


def triage_decision(meta) -> tuple[str, str | None]:
    """('kept'|'dropped', drop_reason). Pure; NULL/unknown fields always pass."""
    if meta is None:
        return "dropped", "no_us_listing"
    if meta.is_game is True:
        return "dropped", "game"
    if (meta.installs or 0) > ESTABLISHED_INSTALLS_MAX:
        return "dropped", "installs_over_threshold"
    if (meta.rating_count or 0) > ESTABLISHED_RATINGS_MAX:
        return "dropped", "ratings_over_threshold"
    return "kept", None


def _app_dict(store: str, store_id: str, meta) -> dict:
    """AppMeta -> upsert_app dict with an EXPLICIT store. (repo._app_dict assumes a
    bare AppMeta is iOS, which would mislabel Android keepers.)"""
    return {"store": store, "store_id": store_id, "name": meta.name,
            "developer": meta.seller, "genre": meta.genre,
            "release_date": meta.release_date, "rating": meta.avg_rating,
            "rating_count": meta.rating_count, "installs": meta.installs,
            "icon_url": meta.icon_url, "screenshots": meta.screenshots,
            "price_label": meta.price_label, "description": meta.description,
            "is_game": meta.is_game}


def _promote(conn, row, meta) -> int:
    advertiser_id = repo.upsert_advertiser(conn, SimpleNamespace(
        page_id=row.page_id, page_name=row.page_name,
        page_like_count=row.page_like_count))
    repo.upsert_app(conn, _app_dict(row.store, row.store_id, meta))
    return advertiser_id


def run_triage(conn, *, lookup_ios, lookup_android) -> dict:
    """Triage every status='new' candidate. lookup_*: callable(ids)->{store_id: AppMeta}.
    conn-first, no commit — the caller owns the transaction."""
    rows = new_candidates(conn)
    stats = {"examined": len(rows), "kept": 0, "dropped": {},
             "lookup_failed_ios": False, "lookup_failed_android": False}
    ios_rows = [r for r in rows if r.store == "ios"]
    android_rows = [r for r in rows if r.store == "android"]

    def _resolve(group, lookup, failed_key):
        ids = [r.store_id for r in group]
        if not ids:
            return {}
        try:
            found = lookup(ids)
        except Exception:                       # network/API failure -> retry tomorrow
            stats[failed_key] = True
            return None
        if not found:                           # sidecar crash / empty API response while
            stats[failed_key] = True            # ids were pending: can't tell dead from down
            return None
        return found

    for group, lookup, failed_key in ((ios_rows, lookup_ios, "lookup_failed_ios"),
                                      (android_rows, lookup_android, "lookup_failed_android")):
        found = _resolve(group, lookup, failed_key)
        if found is None:
            continue                            # whole group stays 'new'
        for row in group:
            meta = found.get(str(row.store_id))
            status, reason = triage_decision(meta)
            if status == "kept":
                advertiser_id = _promote(conn, row, meta)
                mark_triaged(conn, row.id, status="kept",
                             kept_advertiser_id=advertiser_id)
                stats["kept"] += 1
            else:
                mark_triaged(conn, row.id, status="dropped", drop_reason=reason)
                stats["dropped"][reason] = stats["dropped"].get(reason, 0) + 1
    return stats
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it PYTHONPATH=src .venv/bin/python -m pytest tests/discovery/test_triage.py -v`
Expected: 6 PASS

- [ ] **Step 5: Commit**

```bash
git add src/agf/discovery/triage.py tests/discovery/test_triage.py
git commit -m "feat(discovery): stage-2 triage — free store-lookup gate + keeper promotion"
```

---

### Task 6: Worker integration — daily sweep→triage replaces the EU firehose

**Files:**
- Modify: `src/agf/workers/main.py`
- Modify: `src/agf/extract/fake.py` (demo keyword ads)
- Modify: `tests/workers/test_main.py`

**Why this shape:** the worker loop stays hourly, but the sweep is gated to once per
finished `sweep_runs` row per day (same pattern as `refresh_apps`). The EU-firehose
`run_discovery` call and its env knobs are removed from the worker (the module and its
tests stay — it's no longer wired). Enrich keeps refreshing flagged gems (they must not
go stale) but keepers now join the feed, and the fixed `AGF_ENRICH_LIMIT` becomes a
request-budget-derived limit.

- [ ] **Step 1: Update the dry-run fixture in `src/agf/extract/fake.py`**

In `FakeExtractor.demo`, after the `page_ads = {...}` block, add keyword ads and pass
them to the constructor (small page likes so the sweep keeps them):

```python
        def kw_ad(archive, pid, name, link, days_old, likes):
            rec = ad(archive, pid, name, link, days_old)
            rec.page_like_count = likes
            return rec
        keyword = [
            kw_ad("fake-kw-1", "FAKE1", "Demo Sleep",
                  "https://apps.apple.com/us/app/x/id444000", 12, 900),
            kw_ad("fake-kw-2", "FAKE2", "Demo Tutor",
                  "https://apps.apple.com/us/app/x/id555000", 9, 1500),
        ]
        return cls(eu_ads=eu, page_ads=page_ads, default_ads=keyword)
```

(`default_ads` serves every keyword query, so any 2-keyword dry-run sweep folds both
pages, deduped across keywords by the pool's unique key.)

- [ ] **Step 2: Rewrite the worker's discovery step in `src/agf/workers/main.py`**

Replace imports:

```python
# REMOVE:
from agf.discovery.run_discovery import run_discovery
# ADD:
from functools import partial
from agf.db.candidates import (close_sweep_run, kept_advertiser_ids,
                               open_sweep_run, sweep_ran_today)
from agf.discovery.sweep import run_sweep
from agf.discovery.taxonomy import sweep_entries
from agf.discovery.triage import run_triage
from agf.enrich_full.appstore import lookup_google_play, lookup_itunes_batch
```

Add the dry-run lookup stub (module level, near `dry_run_deps_factory`):

```python
def _demo_lookup(ids):
    """Offline AppMeta for any id — lets a dry-run exercise the full triage path."""
    from agf.enrich import AppMeta
    return {str(i): AppMeta(track_id=str(i), name=f"Demo App {i}", seller="Demo Dev",
                            genre="Health & Fitness", release_date="2026-05-01",
                            rating_count=120, avg_rating=4.8, is_game=False)
            for i in ids}
```

Replace `run_once`'s signature and steps 1/3 (steps 1.5–2 stay, with one change noted):

```python
def run_once(engine, extractor, *, now_epoch, as_of, dry_run, http_client=None,
             sweep_pages=2, kw_delay=3.0, enrich_budget=600, enrich_top_n=5,
             enrich_ttl_hours=168, score_after=False, min_score=ALERT_THRESHOLD) -> dict:
    """One sweep→triage→(score)→enrich pass. Returns a summary dict (no printing)."""
    # 1) breadth-first discovery — daily-gated. Sweep commits per keyword; triage is
    #    one txn. A dry-run always sweeps (offline) and never records a sweep_runs row.
    sweep_stats, triage_stats = None, None
    with engine.connect() as conn:
        due = dry_run or not sweep_ran_today(conn, as_of)
    if due:
        run_id = None
        if not dry_run:
            with begin(engine) as conn:
                run_id = open_sweep_run(conn)
        try:
            if dry_run:
                keywords = [e.keyword for e in sweep_entries()[:2]]
                sweep_stats = asyncio.run(run_sweep(
                    engine, extractor, keywords=keywords, max_pages=sweep_pages,
                    kw_delay=0, sleep=lambda s: None))
                lookup_ios = lookup_android = _demo_lookup
            else:
                sweep_stats = asyncio.run(run_sweep(
                    engine, extractor, max_pages=sweep_pages, kw_delay=kw_delay))
                lookup_ios = partial(lookup_itunes_batch, http_client, pause=3.0)
                lookup_android = lookup_google_play
            if dry_run or http_client is not None:
                with begin(engine) as conn:
                    triage_stats = run_triage(conn, lookup_ios=lookup_ios,
                                              lookup_android=lookup_android)
            if run_id is not None:
                with begin(engine) as conn:
                    close_sweep_run(conn, run_id, sweep=sweep_stats, triage=triage_stats)
        except Exception as exc:
            if run_id is not None:
                with begin(engine) as conn:
                    close_sweep_run(conn, run_id, sweep=sweep_stats,
                                    triage=triage_stats, error=repr(exc))
            raise
```

Step 1.5 keeps its comment block but the rollup goes global (the EU confirm-set that
used to scope it no longer exists, and every tracked advertiser must accrue the weekly
series — pure DB, no Meta traffic):

```python
    week_start = as_of - timedelta(days=as_of.weekday())
    with begin(engine) as conn:
        snapshot_weeks = snapshot_advertiser_week(conn, week_start, only_ids=None)
```

Steps 1.6/1.7/2 are unchanged. Step 3 becomes:

```python
    # 3) enrich — keepers first (they have no ad history yet, so they can't score
    #    until enriched), then flagged gems due for their TTL refresh; both under a
    #    proxy-request budget instead of a fixed count. ~14 Meta requests per gem
    #    (12 history pages + creative prefetch).
    est_requests_per_gem = 14
    enrich_limit = max(1, enrich_budget // est_requests_per_gem)
    with engine.connect() as conn:
        kept = kept_advertiser_ids(conn)
        cand_ids = kept if dry_run else list(dict.fromkeys(
            kept + eligible_gem_ids(conn, min_score)))
        if dry_run:
            deps_factory = dry_run_deps_factory(extractor)
        else:
            creative_urls = prefetch_creative_urls(extractor, conn, cand_ids[:enrich_limit])
            deps_factory = production_deps_factory(extractor, http_client, creative_urls)
    due_before = datetime.now(timezone.utc) - timedelta(hours=enrich_ttl_hours)
    enriched = run_worker("enrich", "advertiser",
                          make_enrich_handler(deps_factory, top_n=enrich_top_n),
                          source=engine, candidate_ids=cand_ids,
                          due_before=due_before, limit=enrich_limit)
    return {"sweep": sweep_stats, "triage": triage_stats,
            "snapshot_weeks": snapshot_weeks, "apps_refreshed": apps_refreshed,
            "scored": scored, "enrich_candidates": cand_ids, "enriched": enriched}
```

Update `_summary_line`:

```python
def _summary_line(s: dict) -> str:
    sw, tr = s.get("sweep"), s.get("triage")
    sweep_part = (f"new={sw['candidates_new']} seen={sw['candidates_seen']} "
                  f"kw={sw['keywords_swept']}" if sw else "skipped(daily-gate)")
    kept = tr["kept"] if tr else 0
    line = (f"[worker] sweep: {sweep_part} kept={kept} "
            f"weekly_snapshots={s['snapshot_weeks']} "
            f"apps_refreshed={s.get('apps_refreshed', 0)} "
            f"enriched={len(s['enriched'])}/{len(s['enrich_candidates'])}")
    if s["scored"] is not None:
        line += f" scored={s['scored']}"
    return line
```

Update `main()`'s cfg (replace the old dict) and the module docstring's Env section to
match (remove `AGF_EU_COUNTRIES`, `AGF_EU_MAX_PAGES`, `AGF_MIN_RUNNING_DAYS`,
`AGF_MIN_LONGEVITY_DAYS`, `AGF_ENRICH_LIMIT`; add the three new knobs):

```python
    cfg = dict(
        sweep_pages=_env_int("AGF_SWEEP_PAGES", 2),
        kw_delay=_env_float("AGF_KW_DELAY", 3.0),
        enrich_budget=_env_int("AGF_DISCOVERY_ENRICH_BUDGET", 600),
        enrich_top_n=_env_int("AGF_ENRICH_TOP_N", 5),
        enrich_ttl_hours=_env_int("AGF_ENRICH_TTL_HOURS", 168),
        score_after=_env_bool("AGF_SCORE_AFTER_SWEEP"),
        min_score=_env_float("AGF_MIN_GEM_SCORE", ALERT_THRESHOLD),
    )
```

- [ ] **Step 3: Rewrite `tests/workers/test_main.py` expectations**

Update the module docstring ("build extractor → sweep → triage → enrich keepers"),
add candidates cleanup to `_clean_fake` (after the apps delete):

```python
        conn.execute(text("DELETE FROM discovery.candidates WHERE page_id = ANY(:p)"),
                     {"p": list(FAKE_PAGES)})
        # run_once's triage processes EVERY committed 'new' row; agf_it is a scratch DB,
        # so clear strays other suites may have left, or kept-counts won't be exactly 2.
        conn.execute(text("DELETE FROM discovery.candidates WHERE status = 'new'"))
```

Replace the body of `test_dry_run_sweep_discovers_confirms_and_enriches` (rename to
`test_dry_run_sweep_triages_and_enriches`) — keep the try/finally + `_clean_fake` shape:

```python
        ex = build_extractor(dry_run=True, now_epoch=NOW)   # in-src fake, no network
        summary = run_once(eng, ex, now_epoch=NOW, as_of=date(2026, 6, 4), dry_run=True,
                           score_after=False)
        # sweep: both demo keyword ads fold into NEW candidates (small pages, store links)
        assert summary["sweep"]["candidates_new"] == 2
        assert summary["sweep"]["drops"]["social"] == 0
        # triage: the offline lookup keeps both -> promoted advertisers + apps
        assert summary["triage"]["kept"] == 2 and summary["triage"]["dropped"] == {}
        # enrich: the loop drove the enrich handler over both kept advertisers
        assert len(summary["enrich_candidates"]) == 2
        assert len(summary["enriched"]) == 2
        assert all(isinstance(v, str) for v in summary["enriched"].values())
        with eng.connect() as conn:
            n = conn.execute(text(
                "SELECT count(*) FROM logging.crawl_log WHERE entity_type='advertiser' "
                "AND entity_id = ANY(:i)"), {"i": summary["enrich_candidates"]}).scalar()
            kept_rows = conn.execute(text(
                "SELECT count(*) FROM discovery.candidates "
                "WHERE page_id = ANY(:p) AND status='kept'"), {"p": list(FAKE_PAGES)}).scalar()
            apps = conn.execute(text(
                "SELECT count(*) FROM public.apps WHERE store_id = ANY(:s) AND name IS NOT NULL"),
                {"s": list(FAKE_STORE_IDS)}).scalar()
        assert n == 2 and kept_rows == 2 and apps == 2
        # velocity tracker: global weekly rollup still runs every sweep
        assert summary["snapshot_weeks"] >= 2
```

In `test_dry_run_cli_runs_once_and_exits_zero`, replace the last assertion:

```python
        assert "kept=2" in out.stdout
```

- [ ] **Step 4: Run the worker + discovery + db test dirs**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it PYTHONPATH=src .venv/bin/python -m pytest tests/workers tests/discovery tests/db -v`
Expected: ALL PASS (including the untouched `run_discovery`/firehose unit tests — the module still exists, the worker just no longer calls it). Read the output before the next step.

- [ ] **Step 5: Commit**

```bash
git add src/agf/workers/main.py src/agf/extract/fake.py tests/workers/test_main.py
git commit -m "feat(worker): daily-gated sweep+triage replaces EU firehose; budget-bounded enrich"
```

---

### Task 7: Live e2e smoke (manual gate — requires AGF_PROXIES)

**Files:**
- Create: `scripts/sweep_smoke.py`

- [ ] **Step 1: Write the smoke script**

```python
"""Live one-keyword smoke of sweep -> triage (spec 2026-06-10). Run before calling
the pipeline done — fakes have missed assembly breaks 4x in this repo.

    AGF_PROXIES=http://...@gw.dataimpulse.com:823 \
    AGF_DATABASE_URL=postgresql://agf:agf@localhost:5433/agf \
    PYTHONPATH=src .venv/bin/python scripts/sweep_smoke.py
"""
import asyncio
import os
import sys

import httpx
from sqlalchemy import text

from agf.db.engine import begin, get_engine
from agf.discovery.sweep import run_sweep
from agf.discovery.taxonomy import sweep_entries
from agf.discovery.triage import run_triage
from agf.enrich_full.appstore import lookup_google_play, lookup_itunes_batch
from agf.extract.facade import Extractor
from functools import partial

KEYWORD = os.environ.get("AGF_SMOKE_KEYWORD", "pelvic floor app")

if not os.environ.get("AGF_PROXIES"):
    sys.exit("AGF_PROXIES required (residential).")
engine = get_engine()
entries = [e for e in sweep_entries() if e.keyword == KEYWORD]
assert entries, f"{KEYWORD!r} not in sweep taxonomy"

print(f"sweeping {KEYWORD!r} (US, 2 pages) ...")
stats = asyncio.run(run_sweep(engine, Extractor(), entries=entries))
print("sweep:", stats)

with httpx.Client(timeout=20, follow_redirects=True) as client, begin(engine) as conn:
    tstats = run_triage(conn, lookup_ios=partial(lookup_itunes_batch, client, pause=3.0),
                        lookup_android=lookup_google_play)
print("triage:", tstats)

with engine.connect() as conn:
    rows = conn.execute(text(
        "SELECT page_name, store, store_id, page_like_count, status, drop_reason "
        "FROM discovery.candidates WHERE source_keyword=:k "
        "ORDER BY last_seen_at DESC LIMIT 12"), {"k": KEYWORD}).all()
for r in rows:
    print(f"  {r.status:8} {r.drop_reason or '-':24} {r.store}:{r.store_id:30} "
          f"likes={r.page_like_count} {r.page_name}")
assert stats["candidates_new"] + stats["candidates_seen"] >= 1, "sweep found nothing"
print("SMOKE OK")
```

- [ ] **Step 2: Run it live and READ the output**

Run (with the local stack up — docker pg on :5433, real `AGF_PROXIES`):
`AGF_PROXIES=... AGF_DATABASE_URL=postgresql://agf:agf@localhost:5433/agf PYTHONPATH=src .venv/bin/python scripts/sweep_smoke.py`
Expected: `sweep:` shows ≥1 candidate, `triage:` shows kept/dropped with reasons, candidate rows print, ends `SMOKE OK`. If Meta throttles (1675004), the facade backs off + rotates — re-run once before debugging.

- [ ] **Step 3: Commit**

```bash
git add scripts/sweep_smoke.py
git commit -m "test(discovery): live one-keyword sweep->triage smoke script"
```

---

### Task 8: Runbook + full-suite verification

**Files:**
- Modify: `ops/RUNBOOK.md` (discovery section)
- Modify: `scripts/discover_experiment.py` (docstring note only)

- [ ] **Step 1: Document the new flow in `ops/RUNBOOK.md`**

In the discovery/worker section, replace the EU-firehose description with:

```markdown
## Discovery (breadth-first, 2026-06-10)

Daily, gated by `discovery.sweep_runs` (one finished row per day; the hourly worker
loop checks the gate). Three stages, cheapest first:

1. **Sweep** (proxy): `sweep_entries()` (~260 US app keywords) x `AGF_SWEEP_PAGES`
   (default 2) SERP pages. Drops non-app/social destinations and pages with
   `page_like_count > 10k` (`scoring.vetoes.INCUMBENT_PAGE_LIKES_MAX`) using SERP
   data only. Survivors -> `discovery.candidates` (re-seen rows bump `times_seen`,
   never re-triage).
2. **Triage** (free): iTunes batch + Play sidecar lookups; keeps non-games under
   100k installs / 5k ratings with a live US listing. Keepers are promoted to
   `public.advertisers`/`public.apps` (the tracked pool). Lookup failures stay
   `new` and retry next day.
3. **Enrich** (proxy): keepers first, then TTL-due flagged gems, bounded by
   `AGF_DISCOVERY_ENRICH_BUDGET` (default 600 requests; the worker computes
   `budget // 14` ≈ 42 gems/day at 14 requests per gem).

Audit: `SELECT * FROM discovery.sweep_runs ORDER BY id DESC LIMIT 7;` and
`SELECT drop_reason, count(*) FROM discovery.candidates GROUP BY 1;` answer
"is the gate too tight" with data.

Env: `AGF_SWEEP_PAGES` (2) · `AGF_KW_DELAY` (3.0s) · `AGF_DISCOVERY_ENRICH_BUDGET`
(600) · the EU knobs (`AGF_EU_*`, `AGF_MIN_RUNNING_DAYS`, `AGF_ENRICH_LIMIT`) are
RETIRED — the EU firehose is no longer wired into the worker.
```

Add one line to the top of `scripts/discover_experiment.py`'s docstring:

```
RESEARCH HARNESS ONLY — production discovery is src/agf/discovery/sweep.py + triage.py
(see ops/RUNBOOK.md). This script remains for technique experiments / HTML review.
```

- [ ] **Step 2: Run the FULL suite and read the output**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it PYTHONPATH=src .venv/bin/python -m pytest tests/ -q`
Expected: everything passes (was 248+ tests; now ~270). Investigate ANY failure before committing — especially scoring/api tests that might read worker summaries.

- [ ] **Step 3: Commit**

```bash
git add ops/RUNBOOK.md scripts/discover_experiment.py
git commit -m "docs(ops): breadth-first discovery runbook; mark experiment harness research-only"
```

---

### Task 9: App icons on list items (user request 2026-06-10)

**Files:**
- Create: `db/schema/113_matview_icon_url.sql`
- Modify: `backend/models/models.go` (ShortlistRow struct), `backend/repositories/repositories.go` (mvCols)
- Modify: `web/src/types/contracts.ts`, `web/src/types/api.ts`, `web/src/lib/adapters.ts`
- Modify: `web/src/components/discover/GemCard.tsx`, `web/src/components/saved/SavedCard.tsx`

**Scope note:** icon = `public.apps.icon_url` (already populated by triage promotion and
store refresh — zero new scraping). FB *page* profile pictures are NOT in any payload we
currently parse; they would need a new Meta fetch per page → out of scope, noted for v2.
Fallback when NULL: an initials tile from `app_name`/`page_name` (match the existing
FallbackTile pattern in GemCard).

- [ ] **Step 1: Migration 113** — copy the full matview definition from
  `db/schema/111_emerging_matview_quality.sql`, add `icon_url` to the SELECT from the
  existing apps join (`a.icon_url`), `DROP MATERIALIZED VIEW IF EXISTS` + recreate +
  recreate the unique index exactly as 111 does. Run
  `pytest tests/db/test_migrate.py tests/db/test_models_match_schema.py -v` → PASS.
- [ ] **Step 2: Go layer** — add `IconURL *string \`gorm:"column:icon_url" json:"icon_url"\``
  to ShortlistRow; add `icon_url` to mvCols in repositories.go. Run
  `cd backend && go build ./... && go test ./...` → PASS (integration tests need agf_it on :5433).
- [ ] **Step 3: Web layer** — `icon_url?: string` on both ApiShortlistRow and ShortlistRow
  types; `icon_url: r.icon_url ?? undefined` in adaptShortlistRow; render in GemCard
  header and SavedCard header: 28px rounded icon `<img>` when present, else initials
  tile. Plain alt text ("app icon"). Run `cd web && npx tsc --noEmit && npm run lint` → PASS.
- [ ] **Step 4: Commit**

```bash
git add db/schema/113_matview_icon_url.sql backend/models/models.go backend/repositories/repositories.go \
  web/src/types/contracts.ts web/src/types/api.ts web/src/lib/adapters.ts \
  web/src/components/discover/GemCard.tsx web/src/components/saved/SavedCard.tsx
git commit -m "feat(ui): app icons on discover/saved list items (matview icon_url + initials fallback)"
```

---

### Task 10: Continuous production run on this machine (goal 2026-06-10)

**Files:**
- Create: `ops/.env` (from `ops/.env.example` — NOT committed; verify it's gitignored)

- [ ] **Step 1:** Fill `ops/.env` from the example: `AGF_DATABASE_URL`, `AGF_PROXIES`
  (DataImpulse residential — the working credential), S3/minio keys, `GEMINI_API_KEY`
  (billed key), the new `AGF_SWEEP_PAGES=2` / `AGF_KW_DELAY=3.0` /
  `AGF_DISCOVERY_ENRICH_BUDGET=600`. Confirm `ops/.env` is in `.gitignore`.
- [ ] **Step 2:** Apply migrations to the LIVE db (`PYTHONPATH=src .venv/bin/python -c
  "from agf.db.engine import get_engine; from agf.db.migrate import apply_migrations;
  print(apply_migrations(get_engine()))"` with `AGF_DATABASE_URL` pointing at :5433/agf).
- [ ] **Step 3:** `docker compose -f ops/docker-compose.yml --env-file ops/.env up -d --build`
  → worker + api + pg + minio up. Verify: worker heartbeat file fresh, logs show
  `[worker] starting — mode=LIVE`, and after the first sweep
  `SELECT * FROM discovery.sweep_runs` has a finished row with sane counters.
- [ ] **Step 4:** Refresh the matview + scoring once (`AGF_SCORE_AFTER_SWEEP=1` for the
  first run or `python -m agf.scoring.run_scoring`), so keepers appear in the feed.

### Task 11: UI sanity check (goal 2026-06-10)

- [ ] **Step 1:** Web up in live mode (`NEXT_PUBLIC_DATA_MODE=live`,
  `NEXT_PUBLIC_API_URL=http://localhost:8080/api` — must end in `/api`).
- [ ] **Step 2:** Screenshot the Discover feed (Playwright). Verify with eyes: list items
  show icons (or initials fallback), apps are plausibly EMERGING (small/unknown names,
  not Nike/ChatGPT), growth numbers formatted, store links work.
- [ ] **Step 3:** Report findings to the user with the screenshot; fix obvious data
  nonsense (e.g. games leaking through, incumbents) before calling the goal met.

---

## Post-implementation checklist (not tasks, gates)

- [ ] Live smoke (Task 7) ran against real Meta + stores and printed `SMOKE OK` — required by the repo's live-e2e lesson before claiming done.
- [ ] `sweep_runs` row visible after the first real worker sweep; drop-reason distribution sane (expect `non_app` to dominate).
- [ ] Memory + CONTINUE-PROMPT updated with the new pipeline shape.
