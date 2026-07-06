# Keyword Novelty Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the keyword flywheel re-finding the known corpus (a redundancy gate) and add external keyword sources that reach niches outside the corpus (a breadth engine).

**Architecture:** Every keyword source — the flywheel miner and the new external sources — funnels through one redundancy gate that drops keywords semantically close to already-exhausted basins, then through the existing yield-weighted category budget, then into the existing `dynamic_keywords` trial→active→retired lifecycle. Novelty = cosine distance of a candidate keyword's embedding to the embeddings of keywords we've already worked out.

**Tech Stack:** Python 3.13, SQLAlchemy Core (raw SQL via `text()`), PostgreSQL, Gemini embeddings (`gemini-embedding-001`, 768d, L2-normalized), the vendored `google-play-scraper` node sidecar, pytest.

## Global Constraints

- Module root is the repo; run from repo root. Package under `src/agf/`.
- DB tests require `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it` (a skip is NOT a pass — the root conftest silently skips DB tests when unset).
- Full suite must end 0 failed: `AGF_TEST_DB_URL=…@localhost:55433/agf_it .venv/bin/pytest -q`.
- Embeddings are L2-normalized, so cosine similarity = dot product. Never re-normalize.
- Taste-blind: novelty = semantic distance to exhausted basins, never user ratings.
- The gate must NEVER be able to starve discovery: on embed failure or cold-start (empty basin set) it degrades OPEN (admit by pre-filter order).
- All external fetches (Apple hints, GPlay suggest, Gemini) are isolated: a failure increments `stats['errors']` and skips that source; it never raises out of the stage.
- Commit after each task with explicit paths (never `git add -A`). Commit to `main`.
- Migrations are additive, numbered; latest existing is 145 → this plan adds 146.

---

## File Structure

- Create `db/schema/146_dynamic_keyword_embeddings.sql` — keyword-embedding cache table.
- Create `src/agf/discovery/keyword_redundancy.py` — the gate: basin fetch/cache, scoring, admission.
- Create `src/agf/discovery/store_autocomplete.py` — Apple hints + GPlay suggest → `(phrase, category)` candidates.
- Modify `src/agf/discovery/release_keywords.py` — add `brainstorm_niche_keywords`.
- Modify `src/agf/workers/main.py` — wire the gate into `run_flywheel_stage`; extend the mint stage with the new Pipe-2 sources.
- Modify `src/agf/enrich_full/appstore.py` — add `gplay_suggest` sidecar helper (sits with the other gplay helpers).
- Create `src/agf/enrich_full/sidecar/gp_suggest.js` — sidecar entry for GPlay suggest (mirrors `gp_lookup.js`).
- Tests: `tests/discovery/test_keyword_redundancy.py`, `tests/discovery/test_store_autocomplete.py`, `tests/discovery/test_release_keywords.py` (extend), `tests/workers/test_flywheel_stage.py` (extend), `tests/workers/test_mint_stage.py` (new).

---

# PHASE 1 — The redundancy gate (Pipe 1 fix)

### Task 1: Migration 146 — keyword-embedding cache

**Files:**
- Create: `db/schema/146_dynamic_keyword_embeddings.sql`
- Test: `tests/discovery/test_keyword_redundancy.py`

**Interfaces:**
- Produces: table `discovery.dynamic_keyword_embeddings(keyword_id BIGINT PK, embedding REAL[], content_hash TEXT, dim INT, model TEXT, embedded_at TIMESTAMPTZ)`.

- [ ] **Step 1: Write the migration SQL**

`db/schema/146_dynamic_keyword_embeddings.sql`:
```sql
-- Content embedding (Gemini gemini-embedding-001 @768d, L2-normalized) of a dynamic
-- keyword phrase, cached so the redundancy gate never re-embeds a known keyword.
-- Mirrors discovery.candidate_embeddings (137).
CREATE TABLE IF NOT EXISTS discovery.dynamic_keyword_embeddings (
    keyword_id   BIGINT      PRIMARY KEY
                 REFERENCES discovery.dynamic_keywords(id) ON DELETE CASCADE,
    embedding    REAL[]      NOT NULL,
    content_hash TEXT        NOT NULL,
    dim          INT         NOT NULL,
    model        TEXT        NOT NULL DEFAULT '',
    embedded_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

- [ ] **Step 2: Apply the migration to the test DB**

Run: `PGPASSWORD=agf psql -h localhost -p 55433 -U agf -d agf_it -f db/schema/146_dynamic_keyword_embeddings.sql`
Expected: `CREATE TABLE`

- [ ] **Step 3: Verify the table exists**

Run: `PGPASSWORD=agf psql -h localhost -p 55433 -U agf -d agf_it -c "\d discovery.dynamic_keyword_embeddings"`
Expected: table description listing `keyword_id`, `embedding`, `content_hash`, `dim`, `model`, `embedded_at`.

- [ ] **Step 4: Commit**

```bash
git add db/schema/146_dynamic_keyword_embeddings.sql
git commit -m "feat(discovery): migration 146 dynamic_keyword_embeddings cache"
```

---

### Task 2: Gate pure functions — `redundancy_scores` + `admit_by_novelty`

**Files:**
- Create: `src/agf/discovery/keyword_redundancy.py`
- Test: `tests/discovery/test_keyword_redundancy.py`

**Interfaces:**
- Consumes: `embed_texts(texts, *, _post=None) -> list[list[float]]` from `agf.scoring.embeddings` (L2-normalized 768d).
- Produces:
  - `redundancy_scores(phrases: list[str], basin_vectors: list[list[float]], *, embed=embed_texts) -> dict[str, float]` — max cosine (=dot) of each phrase's embedding to any basin vector; empty basin → all 0.0.
  - `admit_by_novelty(scored: dict[str, float], *, threshold: float, limit: int) -> list[str]` — drop score ≥ threshold, return survivors sorted ascending by score (novel first), capped at limit.

- [ ] **Step 1: Write the failing tests**

`tests/discovery/test_keyword_redundancy.py`:
```python
"""Redundancy gate: scoring (pure, fake embed) and novelty admission (pure)."""
from agf.discovery.keyword_redundancy import admit_by_novelty, redundancy_scores


def _fake_embed(mapping):
    # returns an embed() stub that yields the mapped unit vector per text, in order
    def embed(texts, **kw):
        return [mapping[t] for t in texts]
    return embed


def test_redundancy_scores_max_over_basins():
    # 2-d unit vectors; cosine = dot product (already normalized).
    basins = [[1.0, 0.0], [0.0, 1.0]]
    embed = _fake_embed({"north": [0.0, 1.0], "diag": [0.6, 0.8]})
    scores = redundancy_scores(["north", "diag"], basins, embed=embed)
    assert scores["north"] == 1.0                    # identical to basin 2
    assert abs(scores["diag"] - 0.8) < 1e-9          # max(0.6, 0.8)


def test_redundancy_scores_empty_basin_is_zero():
    embed = _fake_embed({"anything": [1.0, 0.0]})
    assert redundancy_scores(["anything"], [], embed=embed) == {"anything": 0.0}


def test_admit_by_novelty_drops_redundant_and_orders_novel_first():
    scored = {"stale": 0.95, "fresh": 0.10, "mid": 0.50}
    out = admit_by_novelty(scored, threshold=0.90, limit=10)
    assert out == ["fresh", "mid"]                   # stale dropped, ascending score


def test_admit_by_novelty_respects_limit():
    scored = {"a": 0.1, "b": 0.2, "c": 0.3}
    assert admit_by_novelty(scored, threshold=0.9, limit=2) == ["a", "b"]


def test_admit_by_novelty_threshold_is_inclusive_drop():
    assert admit_by_novelty({"x": 0.90}, threshold=0.90, limit=5) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it .venv/bin/pytest tests/discovery/test_keyword_redundancy.py -q`
Expected: FAIL — `ModuleNotFoundError: agf.discovery.keyword_redundancy`.

- [ ] **Step 3: Write the module (pure functions only)**

`src/agf/discovery/keyword_redundancy.py`:
```python
"""Keyword redundancy gate. A candidate keyword semantically close to a basin we've
already mined out is likely to re-find the known corpus, so we drop it before it costs
a sweep. Novelty = 1 - max cosine similarity to any exhausted-basin keyword embedding.
Embeddings are L2-normalized (cosine = dot product). Taste-blind.

See docs/superpowers/specs/2026-07-06-keyword-novelty-engine-design.md."""
from agf.scoring.embeddings import embed_texts


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def redundancy_scores(phrases, basin_vectors, *, embed=embed_texts) -> dict[str, float]:
    """Max cosine similarity of each phrase to any exhausted-basin vector. No basins →
    every phrase scores 0.0 (cold-start admits freely). Embeds all phrases in one call."""
    phrases = list(phrases)
    if not phrases:
        return {}
    vecs = embed(phrases)
    if not basin_vectors:
        return {p: 0.0 for p in phrases}
    return {p: max(_dot(v, b) for b in basin_vectors)
            for p, v in zip(phrases, vecs)}


def admit_by_novelty(scored: dict[str, float], *, threshold: float, limit: int) -> list[str]:
    """Drop phrases scoring >= threshold (too close to an exhausted basin); return the
    rest sorted ascending by score (most novel first), capped at limit."""
    kept = [(p, s) for p, s in scored.items() if s < threshold]
    kept.sort(key=lambda ps: (ps[1], ps[0]))
    return [p for p, _ in kept[:max(0, limit)]]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it .venv/bin/pytest tests/discovery/test_keyword_redundancy.py -q`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/agf/discovery/keyword_redundancy.py tests/discovery/test_keyword_redundancy.py
git commit -m "feat(discovery): keyword redundancy gate — scoring + novelty admission"
```

---

### Task 3: `exhausted_basin_embeddings` — DB read + lazy cache

**Files:**
- Modify: `src/agf/discovery/keyword_redundancy.py`
- Test: `tests/discovery/test_keyword_redundancy.py`

**Interfaces:**
- Consumes: `discovery.dynamic_keywords` (columns `id, keyword, status, dry_streak`), `discovery.dynamic_keyword_embeddings`, `embed_texts`.
- Produces: `exhausted_basin_embeddings(conn, *, dry_floor: int, embed=embed_texts) -> list[list[float]]` — embeddings for retired keywords plus live keywords with `dry_streak >= dry_floor`; lazily embeds+caches any missing.

- [ ] **Step 1: Write the failing test**

Append to `tests/discovery/test_keyword_redundancy.py`:
```python
from sqlalchemy import text
from agf.discovery.keyword_redundancy import exhausted_basin_embeddings


def test_exhausted_basins_select_retired_and_dry_and_cache(pg_conn):
    pg_conn.execute(text(
        "INSERT INTO discovery.dynamic_keywords (id, keyword, source, kind, status, dry_streak)"
        " VALUES "
        "(90001,'dead one','gem_copy','app','retired',0),"
        "(90002,'dry live','gem_copy','app','active',5),"
        "(90003,'fresh live','gem_copy','app','active',0)"))  # excluded: live, low dry
    calls = {"n": 0}

    def embed(texts, **kw):
        calls["n"] += len(texts)
        return [[1.0, 0.0] for _ in texts]

    vecs = exhausted_basin_embeddings(pg_conn, dry_floor=5, embed=embed)
    assert len(vecs) == 2                      # retired + dry-live, not fresh-live
    assert calls["n"] == 2                     # both embedded on first call
    # second call reads from cache — no new embeds
    vecs2 = exhausted_basin_embeddings(pg_conn, dry_floor=5, embed=embed)
    assert len(vecs2) == 2 and calls["n"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it .venv/bin/pytest tests/discovery/test_keyword_redundancy.py::test_exhausted_basins_select_retired_and_dry_and_cache -q`
Expected: FAIL — `ImportError: cannot import name 'exhausted_basin_embeddings'`.

- [ ] **Step 3: Implement the function**

Add to `src/agf/discovery/keyword_redundancy.py` (imports at top: `from sqlalchemy import text`):
```python
def exhausted_basin_embeddings(conn, *, dry_floor, embed=embed_texts) -> list[list[float]]:
    """Embeddings of keywords we've already worked out: status='retired', or live
    (trial/active) with dry_streak >= dry_floor. Reads cached vectors from
    dynamic_keyword_embeddings; lazily embeds + caches any basin keyword not yet cached.
    Returns just the vectors (order irrelevant — the gate takes a max over them)."""
    rows = conn.execute(text(
        "SELECT k.id, k.keyword, e.embedding "
        "FROM discovery.dynamic_keywords k "
        "LEFT JOIN discovery.dynamic_keyword_embeddings e ON e.keyword_id = k.id "
        "WHERE k.status='retired' OR (k.status IN ('trial','active') AND k.dry_streak >= :f)"),
        {"f": dry_floor}).all()
    vectors, missing = [], []
    for r in rows:
        if r.embedding is not None:
            vectors.append([float(x) for x in r.embedding])
        else:
            missing.append((r.id, r.keyword))
    if missing:
        new_vecs = embed([kw for _, kw in missing])
        for (kid, _), vec in zip(missing, new_vecs):
            conn.execute(text(
                "INSERT INTO discovery.dynamic_keyword_embeddings "
                "(keyword_id, embedding, content_hash, dim, model) "
                "VALUES (:i, :e, :h, :d, :m) ON CONFLICT (keyword_id) DO NOTHING"),
                {"i": kid, "e": vec, "h": "", "d": len(vec), "m": "gemini-embedding-001"})
            vectors.append(vec)
    return vectors
```

- [ ] **Step 4: Run test to verify it passes**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it .venv/bin/pytest tests/discovery/test_keyword_redundancy.py -q`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/agf/discovery/keyword_redundancy.py tests/discovery/test_keyword_redundancy.py
git commit -m "feat(discovery): exhausted-basin embedding fetch + lazy cache"
```

---

### Task 4: Wire the gate into the flywheel

**Files:**
- Modify: `src/agf/workers/main.py` (the `run_flywheel_stage` admission block, currently lines 390-394)
- Test: `tests/workers/test_flywheel_stage.py`

**Interfaces:**
- Consumes: `redundancy_scores`, `admit_by_novelty`, `exhausted_basin_embeddings` from `agf.discovery.keyword_redundancy`.
- Produces: `run_flywheel_stage` now admits only novel mined keywords; `stats['mined']` reflects the admitted set. New env: `AGF_KEYWORD_REDUNDANCY_THRESHOLD` (default 0.90), `AGF_KEYWORD_BASIN_DRY_FLOOR` (default `retire_dry_streak()`).

- [ ] **Step 1: Write the failing test**

Append to `tests/workers/test_flywheel_stage.py` (follow the file's existing fixture style — a fake extractor + `_migrated_engine`; if the file gates on `GEMINI_API_KEY`, inject a fake embed via monkeypatch as below):
```python
def test_flywheel_gate_drops_keyword_near_retired_basin(_migrated_engine, monkeypatch):
    import agf.discovery.keyword_redundancy as kr
    from sqlalchemy import text
    eng = _migrated_engine
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM discovery.dynamic_keywords WHERE keyword LIKE 'GATET%'"))
        conn.execute(text(
            "INSERT INTO discovery.dynamic_keywords (keyword, source, kind, status, dry_streak)"
            " VALUES ('GATET retired basin','gem_copy','app','retired',0)"))

    # fake embed: the mined phrase 'GATET twin' is identical to the retired basin vector;
    # 'GATET novel' is orthogonal.
    vecs = {"GATET retired basin": [1.0, 0.0], "GATET twin": [1.0, 0.0],
            "GATET novel": [0.0, 1.0]}
    monkeypatch.setattr(kr, "embed_texts", lambda texts, **kw: [vecs[t] for t in texts])

    admitted = _run_flywheel_admission(eng, ["GATET twin", "GATET novel"])  # helper below
    assert "GATET novel" in admitted
    assert "GATET twin" not in admitted
```

If the flywheel stage is awkward to invoke end-to-end in a unit test (it runs live sweeps), extract the admission decision into a small helper and test THAT directly — see Step 3.

- [ ] **Step 2: Run test to verify it fails**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it .venv/bin/pytest tests/workers/test_flywheel_stage.py::test_flywheel_gate_drops_keyword_near_retired_basin -q`
Expected: FAIL — `_run_flywheel_admission` not defined.

- [ ] **Step 3: Extract an admission helper and use it in the stage**

In `src/agf/workers/main.py`, add near `run_flywheel_stage` (imports at top of file: `from agf.discovery.keyword_redundancy import admit_by_novelty, exhausted_basin_embeddings, redundancy_scores`; `from agf.db.dynamic_keywords import retire_dry_streak`):
```python
def _gate_keywords(engine, phrases, *, limit):
    """Redundancy-gate a candidate keyword list against exhausted basins. Degrades OPEN
    (returns the input, capped) if embedding fails or there are no basins yet — the gate
    must never starve discovery."""
    if not phrases:
        return []
    threshold = _env_float("AGF_KEYWORD_REDUNDANCY_THRESHOLD", 0.90)
    dry_floor = _env_int("AGF_KEYWORD_BASIN_DRY_FLOOR", retire_dry_streak())
    try:
        with begin(engine) as conn:
            basins = exhausted_basin_embeddings(conn, dry_floor=dry_floor)
        scored = redundancy_scores(phrases, basins)
    except Exception as exc:  # embed/API failure — do not block minting
        print(f"[flywheel] redundancy gate degraded open: {exc!r}", flush=True)
        return list(phrases)[:limit]
    return admit_by_novelty(scored, threshold=threshold, limit=limit)
```
Then replace the admission block (currently `main.py:392-394`):
```python
    mined = _gate_keywords(engine, mined, limit=room) if mined else []
    if mined:
        with begin(engine) as conn:
            add_trial_keywords(conn, mined)
```
For the test seam, add:
```python
def _run_flywheel_admission(engine, phrases):
    """Test-only wrapper: gate `phrases` and return the admitted list (no sweeping)."""
    return _gate_keywords(engine, phrases, limit=len(phrases))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it .venv/bin/pytest tests/workers/test_flywheel_stage.py -q`
Expected: all pass (existing + new).

- [ ] **Step 5: Run the full suite**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it .venv/bin/pytest -q`
Expected: 0 failed (skips allowed only at prior baseline — the media-route MinIO skip).

- [ ] **Step 6: Commit**

```bash
git add src/agf/workers/main.py tests/workers/test_flywheel_stage.py
git commit -m "feat(discovery): gate flywheel keyword admission by novelty (Pipe 1 fix)"
```

**PHASE 1 SHIPPABLE.** The bouncer is live; deploy via ops/prod/02_build_image.sh + VM compose pull if shipping now, else continue.

---

# PHASE 2 — Store autocomplete (Pipe 2 core)

### Task 5: Apple search-hints fetch + parse

**Files:**
- Create: `src/agf/discovery/store_autocomplete.py`
- Test: `tests/discovery/test_store_autocomplete.py`

**Interfaces:**
- Consumes: an httpx-like client with `.get(url, headers=…) -> resp` (resp has `.content` bytes); `plistlib` (stdlib).
- Produces: `apple_hints(client, term: str) -> list[str]` — autocomplete terms for a seed; `[]` on any failure.

- [ ] **Step 1: Write the failing test**

`tests/discovery/test_store_autocomplete.py`:
```python
"""Store autocomplete: Apple-hints + GPlay-suggest parsing (fake clients, no network)."""
import plistlib
from agf.discovery.store_autocomplete import apple_hints


class _Resp:
    def __init__(self, content): self.content = content

class _Client:
    def __init__(self, content): self._c = content
    def get(self, url, headers=None): return _Resp(self._c)


def _plist(terms):
    return plistlib.dumps({"hints": [{"term": t} for t in terms]})


def test_apple_hints_parses_terms():
    client = _Client(_plist(["sleep app", "sleep tracker app"]))
    assert apple_hints(client, "sleep") == ["sleep app", "sleep tracker app"]


def test_apple_hints_bad_payload_returns_empty():
    client = _Client(b"not a plist")
    assert apple_hints(client, "sleep") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it .venv/bin/pytest tests/discovery/test_store_autocomplete.py -q`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `apple_hints`**

`src/agf/discovery/store_autocomplete.py`:
```python
"""External keyword vocabulary from app-store search autocomplete (Apple hints + Google
Play suggest). Uncorrelated with our found-ad corpus by construction — this is Pipe 2's
breadth source. Returns normalized 'X app' style phrases. All fetches degrade to [] on
failure (per-seed isolation). See the 2026-07-06 novelty-engine spec."""
import plistlib

_APPLE_HINTS = ("https://search.itunes.apple.com/WebObjects/MZSearchHints.woa/wa/hints"
                "?clientApplication=Software&term={term}")
_APPLE_HEADERS = {"X-Apple-Store-Front": "143441-1,29", "User-Agent": "iTunes/12.11"}


def apple_hints(client, term: str) -> list[str]:
    """Apple App Store search autocomplete terms for `term`. [] on any failure."""
    from urllib.parse import quote
    try:
        resp = client.get(_APPLE_HINTS.format(term=quote(term)), headers=_APPLE_HEADERS)
        data = plistlib.loads(resp.content)
        return [h["term"] for h in data.get("hints", []) if h.get("term")]
    except Exception:
        return []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it .venv/bin/pytest tests/discovery/test_store_autocomplete.py -q`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/agf/discovery/store_autocomplete.py tests/discovery/test_store_autocomplete.py
git commit -m "feat(discovery): Apple search-hints autocomplete fetch"
```

---

### Task 6: Google Play `suggest` sidecar helper

**Files:**
- Create: `src/agf/enrich_full/sidecar/gp_suggest.js`
- Modify: `src/agf/enrich_full/appstore.py` (add `gplay_suggest`, next to `gplay_similar`)
- Test: `tests/discovery/test_store_autocomplete.py`

**Interfaces:**
- Consumes: the vendored `google-play-scraper` (`suggest` method) via a node sidecar, mirroring `gplay_similar`/`_gplay_graph`.
- Produces: `gplay_suggest(term: str, *, runner=None) -> list[str]` — GPlay autocomplete terms; `[]` on failure.

- [ ] **Step 1: Write the sidecar JS**

`src/agf/enrich_full/sidecar/gp_suggest.js` (mirror the existing `gp_graph.js` shape — read one arg, print JSON):
```javascript
// Google Play search autocomplete. Usage: node gp_suggest.js "<term>" -> JSON array of strings.
import gp from 'google-play-scraper';
const g = gp.default || gp;
const term = process.argv[2] || '';
g.suggest({ term })
  .then((r) => { process.stdout.write(JSON.stringify(r || [])); })
  .catch(() => { process.stdout.write('[]'); });
```

- [ ] **Step 2: Write the failing test (fake runner — no node)**

Append to `tests/discovery/test_store_autocomplete.py`:
```python
from agf.enrich_full.appstore import gplay_suggest


def test_gplay_suggest_parses_runner_json():
    runner = lambda term: '["sleep sounds", "sleep tracker", "sleep number"]'
    assert gplay_suggest("sleep", runner=runner) == ["sleep sounds", "sleep tracker", "sleep number"]


def test_gplay_suggest_bad_json_returns_empty():
    assert gplay_suggest("sleep", runner=lambda term: "boom") == []
```

- [ ] **Step 3: Run test to verify it fails**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it .venv/bin/pytest tests/discovery/test_store_autocomplete.py -q`
Expected: FAIL — `ImportError: cannot import name 'gplay_suggest'`.

- [ ] **Step 4: Implement `gplay_suggest`**

Add to `src/agf/enrich_full/appstore.py` (near `gplay_similar`, reuse the module's sidecar-invocation pattern; model the default runner on `_default_node_runner`/`_default_graph_runner` already in the file, pointing at `gp_suggest.js`):
```python
import json


def _default_suggest_runner(term):
    import subprocess
    from pathlib import Path
    script = Path(__file__).parent / "sidecar" / "gp_suggest.js"
    out = subprocess.run(["node", str(script), term], capture_output=True, text=True,
                         timeout=30)
    return out.stdout


def gplay_suggest(term: str, *, runner=None) -> list[str]:
    """Google Play search autocomplete terms for `term`. [] on any failure."""
    runner = runner or _default_suggest_runner
    try:
        data = json.loads(runner(term))
        return [str(t) for t in data if t]
    except Exception:
        return []
```

- [ ] **Step 5: Run test to verify it passes**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it .venv/bin/pytest tests/discovery/test_store_autocomplete.py -q`
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add src/agf/enrich_full/sidecar/gp_suggest.js src/agf/enrich_full/appstore.py tests/discovery/test_store_autocomplete.py
git commit -m "feat(enrich): Google Play suggest sidecar helper"
```

---

### Task 7: Gather autocomplete candidates by category

**Files:**
- Modify: `src/agf/discovery/store_autocomplete.py`
- Test: `tests/discovery/test_store_autocomplete.py`

**Interfaces:**
- Consumes: `apple_hints`, `gplay_suggest`, `NICHE_TERMS_BY_CATEGORY` + `IN_SCOPE_CATEGORIES` (from `agf.discovery.taxonomy`), `_normalize` (from `agf.discovery.release_keywords`).
- Produces: `gather_autocomplete_candidates(client, *, categories, seeds_per_category=3, apple=apple_hints, gplay=gplay_suggest) -> dict[str, list[str]]` — `{category: [normalized phrases]}`, deduped per category.

- [ ] **Step 1: Write the failing test**

Append to `tests/discovery/test_store_autocomplete.py`:
```python
from agf.discovery.store_autocomplete import gather_autocomplete_candidates


def test_gather_unions_apple_and_gplay_normalized_and_deduped():
    apple = lambda client, term: ["Sleep App", "meditation timer app"]
    gplay = lambda term, **kw: ["sleep app", "white noise app"]  # 'sleep app' dup vs Apple
    out = gather_autocomplete_candidates(
        None, categories=["Health & Fitness"], seeds_per_category=1,
        apple=apple, gplay=gplay)
    got = set(out["Health & Fitness"])
    assert "sleep app" in got and "white noise app" in got and "meditation timer app" in got
    # normalized (lowercased) and deduped: 'Sleep App' and 'sleep app' collapse to one
    assert sum(1 for p in out["Health & Fitness"] if p == "sleep app") == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it .venv/bin/pytest tests/discovery/test_store_autocomplete.py::test_gather_unions_apple_and_gplay_normalized_and_deduped -q`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Implement `gather_autocomplete_candidates`**

Add to `src/agf/discovery/store_autocomplete.py`:
```python
from agf.discovery.release_keywords import _normalize
from agf.discovery.taxonomy import IN_SCOPE_CATEGORIES, NICHE_TERMS_BY_CATEGORY


def _seeds_for(category: str, n: int) -> list[str]:
    """Seed terms to expand for a category: the category name + its top taxonomy terms."""
    terms = [str(t) for t in NICHE_TERMS_BY_CATEGORY.get(category, [])][:n]
    return [category.lower(), *terms]


def gather_autocomplete_candidates(client, *, categories, seeds_per_category=3,
                                   apple=apple_hints, gplay=gplay_suggest) -> dict:
    """For each in-scope category, expand its seed terms through Apple + Google Play
    autocomplete, normalize to 'X app' phrases, dedup per category. Each source failure
    degrades to [] (isolated). Zero Meta traffic."""
    out: dict[str, list[str]] = {}
    for cat in categories:
        if cat not in IN_SCOPE_CATEGORIES:
            continue
        raw: list[str] = []
        for seed in _seeds_for(cat, seeds_per_category):
            raw.extend(apple(client, seed))
            raw.extend(gplay(seed))
        seen, phrases = set(), []
        for p in _normalize(raw):
            if p not in seen:
                seen.add(p)
                phrases.append(p)
        if phrases:
            out[cat] = phrases
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it .venv/bin/pytest tests/discovery/test_store_autocomplete.py -q`
Expected: 5 passed.

Note: confirm `_normalize` is importable from `release_keywords` (it is defined there). If it is underscore-private and lint objects, promote it to `normalize_phrases` in `release_keywords.py` and update both call sites in the same commit.

- [ ] **Step 5: Commit**

```bash
git add src/agf/discovery/store_autocomplete.py tests/discovery/test_store_autocomplete.py
git commit -m "feat(discovery): gather autocomplete candidates by category"
```

---

### Task 8: Mint-stage wiring — union sources, gate, budget, admit

**Files:**
- Modify: `src/agf/discovery/frame_mint.py` (add an autocomplete source to the mint flow)
- Modify: `src/agf/workers/main.py` (pass the client / flag through)
- Test: `tests/workers/test_mint_stage.py` (new), `tests/discovery/test_frame_mint.py` (extend)

**Interfaces:**
- Consumes: `gather_autocomplete_candidates`, `_gate_keywords` (reuse from main via a shared gate — see note), `mint_budget`, `add_trial_keywords`.
- Produces: `mint_keywords_from_releases(...)` also admits gated autocomplete keywords with `source='autocomplete:<category>'`; new env `AGF_MINT_AUTOCOMPLETE` (default on).

- [ ] **Step 1: Move the gate into a reusable place**

The gate helper `_gate_keywords` written in Task 4 lives in `main.py`. To reuse it from `frame_mint.py` without a circular import, add a thin gate to `keyword_redundancy.py`:
```python
def gate_phrases(engine, phrases, *, limit, threshold, dry_floor, begin) -> list[str]:
    """Engine-level convenience: fetch basins, score, admit. Degrades OPEN on failure.
    `begin` is the repo's transaction context manager (agf.db ... begin)."""
    if not phrases:
        return []
    try:
        with begin(engine) as conn:
            basins = exhausted_basin_embeddings(conn, dry_floor=dry_floor)
        scored = redundancy_scores(list(phrases), basins)
    except Exception as exc:
        print(f"[gate] degraded open: {exc!r}", flush=True)
        return list(phrases)[:limit]
    return admit_by_novelty(scored, threshold=threshold, limit=limit)
```
Refactor `main.py::_gate_keywords` to call `gate_phrases(engine, phrases, limit=limit, threshold=…, dry_floor=…, begin=begin)`. Keep Task 4's tests green.

- [ ] **Step 2: Write the failing integration test**

`tests/workers/test_mint_stage.py`:
```python
"""Mint stage: autocomplete candidates flow through the redundancy gate + budget into
trials with an autocomplete: source tag; a near-duplicate of a retired basin is dropped."""
from sqlalchemy import text
from agf.discovery.frame_mint import mint_keywords_from_releases
import agf.discovery.keyword_redundancy as kr


def _clean(eng):
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM discovery.dynamic_keywords "
                          "WHERE source LIKE 'autocomplete:%' OR keyword LIKE 'MINTT%'"))


def test_autocomplete_source_admits_novel_and_tags_source(_migrated_engine, monkeypatch):
    eng = _migrated_engine
    _clean(eng)
    try:
        with eng.begin() as conn:
            conn.execute(text(
                "INSERT INTO discovery.dynamic_keywords (keyword, source, kind, status, dry_streak)"
                " VALUES ('MINTT retired','gem_copy','app','retired',0)"))
        # fake embed: 'MINTT twin' == retired basin; 'MINTT novel' orthogonal
        vecs = {"MINTT retired": [1.0, 0.0], "MINTT twin": [1.0, 0.0], "MINTT novel": [0.0, 1.0]}
        monkeypatch.setattr(kr, "embed_texts", lambda texts, **kw: [vecs[t] for t in texts])
        # fake autocomplete gather: one category, two candidate phrases
        monkeypatch.setattr(
            "agf.discovery.frame_mint.gather_autocomplete_candidates",
            lambda client, **kw: {"Health & Fitness": ["MINTT twin", "MINTT novel"]})

        stats = mint_keywords_from_releases(
            eng, client=None, floor=0, total_slots=40,
            fetch=lambda *a, **k: [], lookup=lambda *a, **k: {},  # no new-release path
            autocomplete=True)
        with eng.connect() as conn:
            rows = conn.execute(text(
                "SELECT keyword FROM discovery.dynamic_keywords "
                "WHERE source='autocomplete:Health & Fitness'")).all()
        kws = {r.keyword for r in rows}
        assert "MINTT novel" in kws          # novel admitted
        assert "MINTT twin" not in kws       # redundant dropped
    finally:
        _clean(eng)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it .venv/bin/pytest tests/workers/test_mint_stage.py -q`
Expected: FAIL — `mint_keywords_from_releases` has no `autocomplete` kwarg.

- [ ] **Step 4: Wire autocomplete into `mint_keywords_from_releases`**

In `src/agf/discovery/frame_mint.py`: add imports
```python
from agf.db.engine import begin
from agf.discovery.keyword_redundancy import gate_phrases
from agf.discovery.store_autocomplete import gather_autocomplete_candidates
```
Add param `autocomplete=True` to the signature. Inside the `with engine.begin() as conn:` budget block, after the new-release minting loop, add an autocomplete pass (dedup vs `exclude`, gate, budget-cap per category):
```python
        if autocomplete:
            try:
                ac = gather_autocomplete_candidates(client, categories=list(budget))
            except Exception:
                stats["errors"] += 1
                ac = {}
            for cat, slots in budget.items():
                cands = [p for p in ac.get(cat, []) if p.lower() not in exclude]
                if not cands:
                    continue
                admitted = gate_phrases(
                    engine, cands, limit=slots,
                    threshold=0.90, dry_floor=5, begin=begin)
                admitted = [p for p in admitted if p.lower() not in exclude]
                if not admitted:
                    continue
                added = add_trial_keywords(conn, admitted,
                                           source=f"autocomplete:{cat}", kind="app")
                stats["minted"] += added
                stats.setdefault("autocomplete_by_category", {})[cat] = added
                exclude.update(p.lower() for p in admitted)
```
In `src/agf/workers/main.py`, pass `autocomplete=_env_bool("AGF_MINT_AUTOCOMPLETE", True)` into the `mint_keywords_from_releases(...)` call (the frame-mint stage, ~line 594), and document the env in the header block.

Note on gate-in-open-transaction: `gate_phrases` opens its own `begin(engine)` for basin caching; ensure it does not deadlock with the surrounding `engine.begin()` — if the repo uses a single connection pool that would block, compute basins ONCE before the budget loop (pass `basins`/scored in) rather than per-category. Prefer: fetch basins once at the top of the autocomplete pass, then call `redundancy_scores`/`admit_by_novelty` directly inside the loop (no nested transaction).

- [ ] **Step 5: Run test to verify it passes**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it .venv/bin/pytest tests/workers/test_mint_stage.py tests/discovery/test_frame_mint.py -q`
Expected: all pass.

- [ ] **Step 6: Run full suite**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it .venv/bin/pytest -q`
Expected: 0 failed.

- [ ] **Step 7: Commit**

```bash
git add src/agf/discovery/frame_mint.py src/agf/discovery/keyword_redundancy.py src/agf/workers/main.py tests/workers/test_mint_stage.py
git commit -m "feat(discovery): mint autocomplete keywords through the novelty gate (Pipe 2 core)"
```

**PHASE 2 SHIPPABLE.** External autocomplete breadth is live behind `AGF_MINT_AUTOCOMPLETE`.

---

# PHASE 3 — LLM niche brainstorm (Pipe 2 amplifier)

### Task 9: `brainstorm_niche_keywords`

**Files:**
- Modify: `src/agf/discovery/release_keywords.py`
- Test: `tests/discovery/test_release_keywords.py`

**Interfaces:**
- Consumes: the existing `_SCHEMA`, `_normalize`, `_MODEL`, `_GEN_URL`, `post_json`, `ProposeError` in `release_keywords.py`.
- Produces: `brainstorm_niche_keywords(category: str, seed_listings: list[dict], n: int, *, api_key=None, model=_MODEL, _post=None) -> list[str]` — adjacent-niche search phrases; raises `ProposeError` on failure.

- [ ] **Step 1: Write the failing test**

Append to `tests/discovery/test_release_keywords.py`:
```python
from agf.discovery.release_keywords import brainstorm_niche_keywords


def test_brainstorm_returns_normalized_phrases():
    def fake_post(url, body):
        # prompt must mention the category and an example listing name
        prompt = body["contents"][0]["parts"][0]["text"]
        assert "Sleep" in prompt or "sleep" in prompt
        assert "DreamTrack" in prompt
        return {"candidates": [{"content": {"parts": [
            {"text": '{"phrases": ["lucid dreaming app", "snore detection app"]}'}]}}]}

    out = brainstorm_niche_keywords(
        "Health & Fitness",
        [{"name": "DreamTrack", "description": "sleep cycle tracker"}],
        5, api_key="k", _post=fake_post)
    assert out == ["lucid dreaming app", "snore detection app"]


def test_brainstorm_no_key_raises():
    import pytest
    from agf.discovery.release_keywords import ProposeError
    with pytest.raises(ProposeError):
        brainstorm_niche_keywords("Health & Fitness", [{"name": "X", "description": "y"}],
                                  3, api_key=None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it .venv/bin/pytest tests/discovery/test_release_keywords.py -q`
Expected: FAIL — `ImportError: cannot import name 'brainstorm_niche_keywords'`.

- [ ] **Step 3: Implement `brainstorm_niche_keywords`**

Add to `src/agf/discovery/release_keywords.py` (a sibling of `propose_release_keywords`; reuse `_SCHEMA`, `_normalize`, `_MODEL`, `_GEN_URL`, `post_json`, `ProposeError`):
```python
_BRAINSTORM_PROMPT = (
    "You are finding NEW, emerging mobile apps to discover via Meta Ad Library search. "
    "For the '{category}' category, list up to {n} EMERGING SUB-NICHES that are adjacent "
    "to — but different from — the example app(s) below, and give the search keyword a "
    "person would type to find apps in each sub-niche. Reach into niches the examples do "
    "NOT already name. Each keyword: 2-4 words, lowercase, usually ending in 'app' "
    "(e.g. 'lucid dreaming app', 'snore detection app'). No brand names, no generic "
    "ad-speak. Return only the phrases.\n\nExample app(s):\n{apps}")


def brainstorm_niche_keywords(category, seed_listings, n, *, api_key=None,
                              model=_MODEL, _post=None) -> list[str]:
    """LLM-brainstormed adjacent-niche search keywords for `category`, grounded by a few
    real new-release listings so it stays anchored to a live signal instead of pure
    priors. Raises ProposeError on missing key / bad reply. Sibling of
    propose_release_keywords (which paraphrases the listing; this reaches adjacent niches)."""
    import json
    import os
    if not seed_listings or n <= 0:
        return []
    key = api_key or os.environ.get("GEMINI_API_KEY")
    if not key:
        raise ProposeError("no GEMINI_API_KEY")
    apps = "\n".join(f"- {(l.get('name') or '').strip()}: {(l.get('description') or '')[:200]}"
                     for l in seed_listings[:3])
    body = {
        "contents": [{"parts": [{"text": _BRAINSTORM_PROMPT.format(
            n=n, category=category, apps=apps)}]}],
        "generationConfig": {"responseMimeType": "application/json",
                             "responseSchema": _SCHEMA},
    }
    post = _post or (lambda url, b: post_json(url, b, max_retries=5, timeout=60))
    try:
        resp = post(_GEN_URL.format(model=model, key=key), body)
        text_out = resp["candidates"][0]["content"]["parts"][0]["text"]
        phrases = json.loads(text_out).get("phrases", [])
    except (HttpBackoffError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise ProposeError(f"gemini brainstorm failed: {exc!r}") from exc
    return _normalize(phrases)[:n]
```
(Confirm `HttpBackoffError` is already imported in the file — it is, for `propose_release_keywords`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it .venv/bin/pytest tests/discovery/test_release_keywords.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/agf/discovery/release_keywords.py tests/discovery/test_release_keywords.py
git commit -m "feat(discovery): LLM niche-brainstorm keyword proposer"
```

---

### Task 10: Wire brainstorm into the mint stage

**Files:**
- Modify: `src/agf/discovery/frame_mint.py`
- Modify: `src/agf/workers/main.py`
- Test: `tests/workers/test_mint_stage.py`

**Interfaces:**
- Consumes: `brainstorm_niche_keywords`, the gate, `mint_budget`, `add_trial_keywords`.
- Produces: mint stage also admits gated brainstorm keywords with `source='brainstorm:<category>'`; new env `AGF_MINT_BRAINSTORM` (default on).

- [ ] **Step 1: Write the failing test**

Append to `tests/workers/test_mint_stage.py`:
```python
def test_brainstorm_source_admits_and_tags(_migrated_engine, monkeypatch):
    eng = _migrated_engine
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM discovery.dynamic_keywords WHERE source LIKE 'brainstorm:%'"))
    try:
        monkeypatch.setattr(kr, "embed_texts",
                            lambda texts, **kw: [[0.0, 1.0] for _ in texts])  # all novel
        # seed listings so brainstorm has grounding; fake the proposer
        monkeypatch.setattr(
            "agf.discovery.frame_mint.brainstorm_niche_keywords",
            lambda category, listings, n, **kw: ["MINTT brainstormed app"][:n])
        stats = mint_keywords_from_releases(
            eng, client=None, floor=0, total_slots=40,
            fetch=lambda *a, **k: [{"store_id": "MINTTB", "name": "S", "genre": "Lifestyle"}],
            lookup=lambda *a, **k: {"MINTTB": _mk_meta("MINTTB", "S", "Social Networking", "d")},
            autocomplete=False, brainstorm=True)
        with eng.connect() as conn:
            rows = conn.execute(text(
                "SELECT keyword FROM discovery.dynamic_keywords WHERE source LIKE 'brainstorm:%'")).all()
        assert any("brainstormed" in r.keyword for r in rows)
    finally:
        with eng.begin() as conn:
            conn.execute(text("DELETE FROM discovery.dynamic_keywords WHERE source LIKE 'brainstorm:%'"))
```
(Reuse the `_meta`/`AppMeta` helper already in `test_frame_mint.py`; import or replicate a small `_mk_meta`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it .venv/bin/pytest tests/workers/test_mint_stage.py::test_brainstorm_source_admits_and_tags -q`
Expected: FAIL — no `brainstorm` kwarg.

- [ ] **Step 3: Wire brainstorm into `mint_keywords_from_releases`**

Add param `brainstorm=True`. Reuse the per-category `by_cat` listings already built for the new-release proposer as the brainstorm seed. In the budget loop (or a parallel pass), for each category with a slot and seed listings:
```python
        if brainstorm:
            for cat, slots in budget.items():
                listings = by_cat.get(cat)
                if not listings:
                    continue
                try:
                    proposed = brainstorm_niche_keywords(cat, listings, slots)
                except ProposeError:
                    stats["errors"] += 1
                    continue
                cands = [p for p in proposed if p.lower() not in exclude]
                admitted = [p for p in _admit_against(basins, cands, slots)  # helper: score+admit
                            if p.lower() not in exclude]
                if not admitted:
                    continue
                added = add_trial_keywords(conn, admitted, source=f"brainstorm:{cat}", kind="app")
                stats["minted"] += added
                stats.setdefault("brainstorm_by_category", {})[cat] = added
                exclude.update(p.lower() for p in admitted)
```
Where `_admit_against(basins, phrases, limit)` = `admit_by_novelty(redundancy_scores(phrases, basins), threshold=…, limit=limit)` using the basins fetched ONCE at the top of the mint block (per Task 8's single-fetch note). Import `brainstorm_niche_keywords` and `ProposeError` in `frame_mint.py`. In `main.py`, pass `brainstorm=_env_bool("AGF_MINT_BRAINSTORM", True)` and document the env.

- [ ] **Step 4: Run test to verify it passes**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it .venv/bin/pytest tests/workers/test_mint_stage.py -q`
Expected: all pass.

- [ ] **Step 5: Run full suite**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it .venv/bin/pytest -q`
Expected: 0 failed.

- [ ] **Step 6: Commit**

```bash
git add src/agf/discovery/frame_mint.py src/agf/workers/main.py tests/workers/test_mint_stage.py
git commit -m "feat(discovery): mint LLM-brainstorm keywords through the gate (Pipe 2 amplifier)"
```

---

## Live end-to-end verification (after all phases, controller-run)

- [ ] Build image from a clean HEAD worktree: `bash ops/prod/02_build_image.sh`.
- [ ] VM pull+restart: `gcloud compute ssh agf-worker --zone=europe-west1-b --project=peon-489013 --command='sudo docker compose -f /opt/agf/docker-compose.yml pull worker cron && sudo docker compose -f /opt/agf/docker-compose.yml up -d worker cron'`.
- [ ] One-off mint cycle in the deployed container (base64-piped script via `docker compose exec -T worker python -`, as in the 2026-07-06 mint verify): assert Apple hints + GPlay suggest return real terms, keywords land as trials tagged `autocomplete:*` / `brainstorm:*`, gate drops at least one near-duplicate, `errors == 0`.
- [ ] Confirm worker healthy: `docker compose ps` shows `worker ... (healthy)`.

## Success criteria (from spec)
- Gate: minted/mined keywords show a lower re-find rate than baseline; near-duplicates of retired basins stop being admitted.
- Breadth: a rising share of new kept apps arrive via `autocomplete:*` / `brainstorm:*` source tags.
- No regression in total kept/day; 0 failing tests; live cycle admits real terms with 0 errors.

---

## Self-review notes (author)
- **Spec coverage:** Part A (gate) = Tasks 1-4; Part B autocomplete = Tasks 5-8; Part B brainstorm = Tasks 9-10; Apple-new-releases unchanged (existing frame_mint); GPlay-new-releases explicitly dropped (spec B1). Degrade-open, per-source isolation, source tags, budget reuse all covered.
- **Open implementation risk flagged in-plan:** nested transaction when the gate opens `begin(engine)` inside the mint stage's `engine.begin()` — Task 8 Step 4 and Task 10 Step 3 mandate fetching basins ONCE before the loop and scoring inline to avoid it. The implementer MUST confirm the repo's `begin()`/session import path (`agf.db.session` is a guess) before Task 8.
- **Import paths resolved (2026-07-06):** `begin` = `from agf.db.engine import begin`; `HttpBackoffError` + `post_json` already imported in `release_keywords.py:16`; `_normalize` defined at `release_keywords.py:43` (Task 7 Step 4 note stands — promote to a public name only if lint objects to the cross-module underscore import).
