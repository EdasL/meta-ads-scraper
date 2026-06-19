# Triage Prior — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Spend the metered enrich budget on candidates whose fetched store listing embeds nearest the user's liked centroid first, keeping a 25% oldest-first explore reserve.

**Architecture:** For each kept candidate, fetch its real store listing by `store_id` (batch iTunes Lookup + Play sidecar), embed `name + genre + description` with the SAME Spec 2 `build_embed_text`/Gemini embedder (cache, embed-once), then reorder `kept_advertiser_ids` by cosine-to-liked-direction with an epsilon-greedy explore reserve — in a best-effort worker stage that falls back to today's FIFO on cold-start or any error.

**Tech Stack:** Python 3.13, SQLAlchemy (psycopg v3), numpy, Gemini REST via Spec 2 `agf.scoring.embeddings`, store fetch via `agf.enrich_full.appstore` (`lookup_itunes_batch`, `lookup_google_play`). Postgres `db/schema/*.sql`.

## Global Constraints
- All work on `main`; commit per task with EXPLICIT paths (never `git add -A`).
- DB/IT tests skip silently without `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it` — a skip is NOT a pass; run the FULL suite to 0 failed.
- Prod DB: `AGF_DATABASE_URL=postgresql://agf:agf@localhost:5433/agf`. Migrations via `agf.db.migrate.apply_migrations` (lexical `db/schema/*.sql`, idempotent). Latest existing = **136**; new = **137**.
- Reuse Spec 2: `agf.scoring.embeddings.embed_texts` (Gemini gemini-embedding-001 @768d, L2-normalized, 429 backoff via `agf.net.http_backoff`), `content_hash`, `build_embed_text(app_name, tagline, genre, description, ad_copy)`; `agf.scoring.content_preference.ContentPreferenceModel` (MIN_LIKES=8, direction = `mean(liked)−mean(disliked)`).
- Reuse store fetch: `agf.enrich_full.appstore.lookup_itunes_batch(client, ids, *, chunk=200, country="us") -> {track_id(str): AppMeta}` and `lookup_google_play(package_ids, *, runner=None) -> {package_id(str): AppMeta}`. `AppMeta` (`agf.enrich`) has `.name`, `.genre`, `.description`, `.track_id` (str). The candidate `store_id` is a str matching `AppMeta.track_id`.
- The triage prior is ADVISORY: any failure or cold-start → fall back to `kept_advertiser_ids` (today's FIFO). Enrich correctness must never depend on it.
- Env: `AGF_TRIAGE_PRIOR` (default on, "1"), `AGF_TRIAGE_EXPLORE_FRAC` (default 0.25), `AGF_TRIAGE_USER` (default `founder@fielddesk.local`).
- SHARED REPO: a parallel session is active — re-read before edit, stage explicit paths, leave web/* + discovery/metrics WIP alone.

## File Structure
| File | Responsibility |
|---|---|
| `db/schema/137_candidate_embeddings.sql` | `discovery.candidate_embeddings` table |
| `src/agf/scoring/content_preference.py` | `direction` property |
| `src/agf/discovery/candidate_embed.py` | `embed_candidates` — fetch listing by id + embed (embed-once) |
| `scripts/embed_candidates.py` | runnable embed entrypoint |
| `src/agf/discovery/triage_prior.py` | pure `liked_direction` + `prioritize` |
| `src/agf/db/candidates.py` | `kept_with_embeddings`, `taste_prioritized_kept` |
| `src/agf/workers/main.py` | embed-candidates stage + reorder `kept` |

**Grounded facts:** `kept_advertiser_ids` `ORDER BY MIN(triaged_at)` at `candidates.py:47`; worker selection `kept = kept_advertiser_ids(conn)` at `main.py:644`, with `http_client` (httpx) in scope. `ContentPreferenceModel.__init__(version, direction=None,…)`, `_dir` at `content_preference.py:41`, `MIN_LIKES=8`. `lookup_itunes_batch` at `appstore.py:92`, `lookup_google_play` at `appstore.py:118`. Every app candidate has `store ∈ {ios,android}` + `store_id` (str).

---

### Task 1: Migration 137 — candidate_embeddings

**Files:**
- Create: `db/schema/137_candidate_embeddings.sql`
- Test: `tests/db/test_candidate_embeddings_migration.py`

**Interfaces:**
- Produces: `discovery.candidate_embeddings(candidate_id BIGINT PK → candidates.id ON DELETE CASCADE, embedding REAL[], content_hash TEXT, dim INT, model TEXT, embedded_at TIMESTAMPTZ)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/db/test_candidate_embeddings_migration.py
import os
import pytest
from sqlalchemy import text
from agf.db.engine import get_engine
from agf.db.migrate import apply_migrations

pytestmark = pytest.mark.skipif(
    not os.environ.get("AGF_TEST_DB_URL"), reason="needs AGF_TEST_DB_URL")


def test_candidate_embeddings_shape_and_cascade():
    eng = get_engine(os.environ["AGF_TEST_DB_URL"])
    apply_migrations(eng)
    with eng.begin() as c:
        cols = {r[0] for r in c.execute(text(
            "SELECT attname FROM pg_attribute "
            "WHERE attrelid='discovery.candidate_embeddings'::regclass "
            "AND attnum>0 AND NOT attisdropped"))}
        assert {"candidate_id", "embedding", "content_hash", "dim",
                "model", "embedded_at"} <= cols
        # FK + CASCADE: insert a candidate + embedding, delete candidate, embedding gone
        cid = c.execute(text(
            "INSERT INTO discovery.candidates (page_id,page_name,store,store_id,"
            "source_keyword,technique,status) VALUES "
            "('M137','N','ios','9001','kw','niche','kept') RETURNING id")).scalar()
        c.execute(text(
            "INSERT INTO discovery.candidate_embeddings "
            "(candidate_id,embedding,content_hash,dim,model) "
            "VALUES (:i, ARRAY[0.1,0.2]::real[], 'h', 2, 't')"), {"i": cid})
        c.execute(text("DELETE FROM discovery.candidates WHERE id=:i"), {"i": cid})
        left = c.execute(text(
            "SELECT count(*) FROM discovery.candidate_embeddings WHERE candidate_id=:i"),
            {"i": cid}).scalar()
        assert left == 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it PYTHONPATH=src .venv/bin/python -m pytest tests/db/test_candidate_embeddings_migration.py -v`
Expected: FAIL (relation `discovery.candidate_embeddings` does not exist).

- [ ] **Step 3: Write the migration**

```sql
-- db/schema/137_candidate_embeddings.sql
-- Content embedding (Gemini gemini-embedding-001 @768d, L2-normalized) of a kept
-- candidate's FETCHED store listing (name + genre + description), written by
-- scripts/embed_candidates.py. The triage prior cosine-ranks these vs the user's
-- liked centroid to prioritize the enrich budget. CASCADE so it dies with the candidate.
CREATE TABLE IF NOT EXISTS discovery.candidate_embeddings (
    candidate_id BIGINT PRIMARY KEY
        REFERENCES discovery.candidates(id) ON DELETE CASCADE,
    embedding    REAL[]      NOT NULL,
    content_hash TEXT        NOT NULL,
    dim          INT         NOT NULL,
    model        TEXT        NOT NULL DEFAULT '',
    embedded_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

- [ ] **Step 4: Run to verify it passes**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it PYTHONPATH=src .venv/bin/python -m pytest tests/db/test_candidate_embeddings_migration.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add db/schema/137_candidate_embeddings.sql tests/db/test_candidate_embeddings_migration.py
git commit -m "feat(db): discovery.candidate_embeddings (migration 137)"
```

---

### Task 2: `direction` accessor on ContentPreferenceModel

**Files:**
- Modify: `src/agf/scoring/content_preference.py`
- Test: `tests/scoring/test_content_preference_direction.py`

**Interfaces:**
- Produces: `ContentPreferenceModel.direction -> np.ndarray | None` (the fitted `_dir`; `None` in cold-start).

- [ ] **Step 1: Write the failing test**

```python
# tests/scoring/test_content_preference_direction.py
import numpy as np
from agf.scoring.content_preference import ContentPreferenceModel, MIN_LIKES


def _unit(v):
    v = np.asarray(v, float); return list(v / np.linalg.norm(v))


def _make(n, base, name):
    return [(i, f"{name}{i}", _unit(np.array(base, float) + 0.01 * (i % 3)))
            for i in range(n)]


def test_direction_none_in_coldstart():
    m = ContentPreferenceModel.fit(liked=_make(2, [1, 0, 0], "L"),
                                   disliked=_make(20, [0, 1, 0], "D"))
    assert m.version == "coldstart"
    assert m.direction is None


def test_direction_is_unit_vector_when_fit():
    m = ContentPreferenceModel.fit(liked=_make(MIN_LIKES, [1, 0, 0], "L"),
                                   disliked=_make(20, [0, 1, 0], "D"))
    assert m.direction is not None
    assert abs(float(np.linalg.norm(m.direction)) - 1.0) < 1e-6
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/scoring/test_content_preference_direction.py -v`
Expected: FAIL (`AttributeError: ... 'direction'`).

- [ ] **Step 3: Add the property**

In `src/agf/scoring/content_preference.py`, add to the class (right after `__init__`):

```python
    @property
    def direction(self):
        """The fitted like−dislike unit vector, or None in cold-start."""
        return self._dir
```

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/scoring/test_content_preference_direction.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agf/scoring/content_preference.py tests/scoring/test_content_preference_direction.py
git commit -m "feat(scoring): expose ContentPreferenceModel.direction"
```

---

### Task 3: Candidate store-fetch + embed

**Files:**
- Create: `src/agf/discovery/candidate_embed.py`, `scripts/embed_candidates.py`
- Test: `tests/discovery/test_candidate_embed.py`

**Interfaces:**
- Consumes: `appstore.lookup_itunes_batch`, `appstore.lookup_google_play`, `AppMeta`; `embeddings.embed_texts`, `build_embed_text`, `content_hash`; `discovery.candidate_embeddings` (Task 1).
- Produces: `embed_candidates(conn, *, itunes_client, gplay_runner=None, lookup_itunes=lookup_itunes_batch, lookup_play=lookup_google_play, embed_fn=embed_texts, commit_every=25, limit=None) -> dict` — fetches + embeds kept candidates **not yet embedded**, upserts `candidate_embeddings`. Lookups + embed are injectable for tests.

- [ ] **Step 1: Write the failing test**

```python
# tests/discovery/test_candidate_embed.py
import os
import pytest
from sqlalchemy import text
from agf.db.engine import get_engine
from agf.db.migrate import apply_migrations
from agf.enrich import AppMeta
from agf.discovery.candidate_embed import embed_candidates

pytestmark = pytest.mark.skipif(
    not os.environ.get("AGF_TEST_DB_URL"), reason="needs AGF_TEST_DB_URL")


def _meta(tid, name, desc):
    return AppMeta(track_id=tid, name=name, seller="", genre="Music",
                   release_date="", rating_count=0, description=desc)


def test_embed_candidates_fetches_embeds_once_and_cascades():
    eng = get_engine(os.environ["AGF_TEST_DB_URL"])
    apply_migrations(eng)
    fake_itunes = lambda client, ids, **kw: {"7777": _meta("7777", "VocalAI", "AI vocal remover")}
    fake_embed = lambda texts, **kw: [[1.0] + [0.0] * 767 for _ in texts]
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM discovery.candidates WHERE page_id='TPC1'"))
        cid = conn.execute(text(
            "INSERT INTO discovery.candidates (page_id,page_name,store,store_id,"
            "source_keyword,technique,status) VALUES "
            "('TPC1','VocalAI','ios','7777','vocal','niche','kept') RETURNING id")).scalar()
        first = embed_candidates(conn, itunes_client=None,
                                 lookup_itunes=fake_itunes, embed_fn=fake_embed)
        assert first["embedded"] == 1
        row = conn.execute(text(
            "SELECT dim FROM discovery.candidate_embeddings WHERE candidate_id=:i"),
            {"i": cid}).first()
        assert row is not None and row.dim == 768
        second = embed_candidates(conn, itunes_client=None,           # already embedded
                                  lookup_itunes=fake_itunes, embed_fn=fake_embed)
        assert second["embedded"] == 0
        conn.execute(text("DELETE FROM discovery.candidates WHERE id=:i"), {"i": cid})
        gone = conn.execute(text(
            "SELECT count(*) FROM discovery.candidate_embeddings WHERE candidate_id=:i"),
            {"i": cid}).scalar()
        assert gone == 0


def test_unfetchable_candidate_skipped_not_crashed():
    eng = get_engine(os.environ["AGF_TEST_DB_URL"])
    apply_migrations(eng)
    empty_itunes = lambda client, ids, **kw: {}      # listing not found
    fake_embed = lambda texts, **kw: [[1.0] + [0.0] * 767 for _ in texts]
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM discovery.candidates WHERE page_id='TPC2'"))
        cid = conn.execute(text(
            "INSERT INTO discovery.candidates (page_id,page_name,store,store_id,"
            "source_keyword,technique,status) VALUES "
            "('TPC2','Gone','ios','404','k','niche','kept') RETURNING id")).scalar()
        out = embed_candidates(conn, itunes_client=None,
                               lookup_itunes=empty_itunes, embed_fn=fake_embed)
        assert out["embedded"] == 0                  # skipped, no crash
        conn.execute(text("DELETE FROM discovery.candidates WHERE id=:i"), {"i": cid})
```

- [ ] **Step 2: Run to verify it fails**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it PYTHONPATH=src .venv/bin/python -m pytest tests/discovery/test_candidate_embed.py -v`
Expected: FAIL (`No module named agf.discovery.candidate_embed`).

- [ ] **Step 3: Write the module + script**

```python
# src/agf/discovery/candidate_embed.py
"""Fetch each kept candidate's real store listing by store_id (batch iTunes Lookup +
Play sidecar) and embed name+genre+description with the SAME Spec 2 builder, so the
triage prior can cosine-rank candidates vs the user's liked centroid. Embed-once:
a candidate already in candidate_embeddings is skipped (no re-fetch)."""
from __future__ import annotations

from sqlalchemy import text

from agf.enrich_full.appstore import lookup_google_play, lookup_itunes_batch
from agf.scoring.embeddings import build_embed_text, content_hash, embed_texts

_PENDING = text(
    "SELECT c.id, c.store, c.store_id FROM discovery.candidates c "
    "WHERE c.status='kept' AND c.store_id IS NOT NULL AND NOT EXISTS ("
    "  SELECT 1 FROM discovery.candidate_embeddings e WHERE e.candidate_id = c.id)")

_UPSERT = text(
    "INSERT INTO discovery.candidate_embeddings "
    "(candidate_id, embedding, content_hash, dim, model, embedded_at) "
    "VALUES (:c, :e, :h, :d, :m, now()) "
    "ON CONFLICT (candidate_id) DO UPDATE SET "
    "  embedding=EXCLUDED.embedding, content_hash=EXCLUDED.content_hash, "
    "  dim=EXCLUDED.dim, model=EXCLUDED.model, embedded_at=now()")


def embed_candidates(conn, *, itunes_client, gplay_runner=None,
                     lookup_itunes=lookup_itunes_batch, lookup_play=lookup_google_play,
                     embed_fn=embed_texts, commit_every=25, limit=None) -> dict:
    rows = conn.execute(_PENDING).all()
    if limit:
        rows = rows[:limit]
    ios = [r.store_id for r in rows if r.store == "ios"]
    android = [r.store_id for r in rows if r.store == "android"]
    meta: dict = {}
    if ios:
        meta.update(lookup_itunes(itunes_client, ios))          # {track_id(str): AppMeta}
    if android:
        meta.update(lookup_play(android, runner=gplay_runner))  # {package_id(str): AppMeta}

    embedded = 0
    for r in rows:
        m = meta.get(str(r.store_id))
        if m is None:                                            # delisted / not found
            continue
        t = build_embed_text(app_name=m.name or "", tagline=None,
                             genre=m.genre, description=m.description, ad_copy=[])
        if not t.strip():
            continue
        vec = embed_fn([t])[0]
        conn.execute(_UPSERT, {"c": r.id, "e": vec, "h": content_hash(t),
                               "d": len(vec), "m": "gemini-embedding-001"})
        embedded += 1
        if commit_every and embedded % commit_every == 0:
            conn.commit()
    return {"embedded": embedded, "fetched": len(meta), "pending": len(rows)}
```

```python
# scripts/embed_candidates.py
"""Fetch + embed kept candidates' store listings into discovery.candidate_embeddings.

Run:
    PYTHONPATH=src AGF_DATABASE_URL=postgresql://agf:agf@localhost:5433/agf \\
    .venv/bin/python scripts/embed_candidates.py
"""
import sys

import httpx

from agf.db.engine import get_engine
from agf.discovery.candidate_embed import embed_candidates


def main() -> int:
    eng = get_engine()
    with httpx.Client(timeout=30.0, headers={"User-Agent": "Mozilla/5.0"}) as client, \
            eng.connect() as conn:
        try:
            stats = embed_candidates(conn, itunes_client=client, commit_every=25)
            conn.commit()
        except Exception as exc:
            conn.commit()
            print(f"[embed_cand] stopped early ({exc!r}); progress saved", flush=True)
            return 1
    print(f"[embed_cand] {stats}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run to verify it passes**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it PYTHONPATH=src .venv/bin/python -m pytest tests/discovery/test_candidate_embed.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add src/agf/discovery/candidate_embed.py scripts/embed_candidates.py tests/discovery/test_candidate_embed.py
git commit -m "feat(discovery): fetch candidate store listing by id + embed (embed-once)"
```

---

### Task 4: `prioritize` — taste order with explore reserve (pure)

**Files:**
- Create: `src/agf/discovery/triage_prior.py`
- Test: `tests/discovery/test_triage_prior.py`

**Interfaces:**
- Consumes: `ContentPreferenceModel.fit(...).direction` (Task 2).
- Produces: `liked_direction(liked, disliked) -> np.ndarray | None`; `prioritize(fifo_order: list[int], emb_by_id: dict[int, list[float]], direction, explore_frac: float = 0.25) -> list[int]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/discovery/test_triage_prior.py
import numpy as np
from agf.discovery.triage_prior import prioritize, liked_direction


def test_passthrough_when_no_direction():
    order = [1, 2, 3, 4]
    assert prioritize(order, {1: [1, 0]}, None) == order


def test_high_cosine_leads_and_explore_reserve_present():
    fifo = [10, 11, 12, 13, 14, 15, 16, 17]      # oldest-first
    d = np.array([1.0, 0.0])
    emb = {a: [(a - 9) / 10.0, 0.5] for a in fifo}   # 17 most aligned, 10 least
    out = prioritize(fifo, emb, d, explore_frac=0.25)
    assert set(out) == set(fifo)                  # nothing dropped
    assert out[0] == 17                           # top priority leads
    assert 10 in out[:5]                          # oldest FIFO surfaces via explore reserve


def test_no_embedding_sinks_but_not_dropped():
    out = prioritize([1, 2, 3], {2: [1.0, 0.0]}, np.array([1.0, 0.0]), explore_frac=0.0)
    assert out[0] == 2 and set(out) == {1, 2, 3}


def test_deterministic():
    fifo = [5, 6, 7, 8]
    d = np.array([1.0, 0.0])
    emb = {a: [a / 10.0, 0.0] for a in fifo}
    assert prioritize(fifo, emb, d) == prioritize(fifo, emb, d)


def test_liked_direction_coldstart_none():
    liked = [(i, f"L{i}", [1.0, 0.0]) for i in range(2)]
    disliked = [(i, f"D{i}", [0.0, 1.0]) for i in range(10)]
    assert liked_direction(liked, disliked) is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/discovery/test_triage_prior.py -v`
Expected: FAIL (`No module named agf.discovery.triage_prior`).

- [ ] **Step 3: Write the module**

```python
# src/agf/discovery/triage_prior.py
"""Reorder the kept-candidate enrich queue by cosine to the user's liked direction,
keeping an epsilon-greedy explore reserve (oldest-first, taste-blind) so the budget
never tunnels on one niche. Pure — no DB. Advisory: callers fall back to FIFO."""
from __future__ import annotations

import numpy as np

from agf.scoring.content_preference import ContentPreferenceModel


def liked_direction(liked, disliked):
    """liked/disliked: list[(id, name, unit_vec)] -> unit direction or None (cold-start)."""
    return ContentPreferenceModel.fit(liked, disliked).direction


def prioritize(fifo_order, emb_by_id, direction, explore_frac=0.25):
    """fifo_order = advertiser ids oldest-first. Returns a reordering: priority slots
    by cosine(emb, direction) desc, with ~explore_frac of slots filled oldest-first.
    direction None / no embeddings -> fifo_order unchanged. Nothing is dropped."""
    if direction is None or not emb_by_id:
        return list(fifo_order)
    d = np.asarray(direction, float)

    def score(a):
        v = emb_by_id.get(a)
        return float(np.dot(np.asarray(v, float), d)) if v is not None else float("-inf")

    priority = sorted(fifo_order, key=score, reverse=True)
    explore_every = max(2, round(1 / explore_frac)) if explore_frac > 0 else 0
    out, seen = [], set()
    pi = fi = 0

    def _nxt(lst, i):
        while i < len(lst) and lst[i] in seen:
            i += 1
        return i

    while len(out) < len(fifo_order):
        use_explore = explore_every and (len(out) % explore_every == explore_every - 1)
        pick = None
        if use_explore:
            fi = _nxt(fifo_order, fi)
            if fi < len(fifo_order):
                pick = fifo_order[fi]
        if pick is None:
            pi = _nxt(priority, pi)
            if pi < len(priority):
                pick = priority[pi]
        if pick is None:                       # priority exhausted -> remaining FIFO
            fi = _nxt(fifo_order, fi)
            pick = fifo_order[fi]
        seen.add(pick)
        out.append(pick)
    return out
```

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/discovery/test_triage_prior.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agf/discovery/triage_prior.py tests/discovery/test_triage_prior.py
git commit -m "feat(discovery): triage-prior ordering with epsilon-greedy explore reserve"
```

---

### Task 5: `taste_prioritized_kept` + worker wiring

**Files:**
- Modify: `src/agf/db/candidates.py`, `src/agf/workers/main.py`
- Test: `tests/db/test_taste_prioritized_kept.py`

**Interfaces:**
- Consumes: `prioritize`, `liked_direction` (Task 4); `kept_advertiser_ids` (existing); `discovery.candidate_embeddings`; `frontend.app_embeddings` + `frontend.app_ratings`; `embed_candidates` (Task 3).
- Produces: `kept_with_embeddings(conn) -> dict[int, list[float]]` (kept_advertiser_id → candidate embedding); `taste_prioritized_kept(conn, *, user, explore_frac) -> list[int]` (reordered advertiser ids; FIFO fallback on cold-start/empty).

- [ ] **Step 1: Write the failing test**

```python
# tests/db/test_taste_prioritized_kept.py
import os
import pytest
from sqlalchemy import text
from agf.db.engine import get_engine
from agf.db.migrate import apply_migrations
from agf.db.candidates import taste_prioritized_kept, kept_advertiser_ids

pytestmark = pytest.mark.skipif(
    not os.environ.get("AGF_TEST_DB_URL"), reason="needs AGF_TEST_DB_URL")


def test_falls_back_to_fifo_when_user_has_no_likes():
    eng = get_engine(os.environ["AGF_TEST_DB_URL"])
    apply_migrations(eng)
    with eng.begin() as conn:
        fifo = kept_advertiser_ids(conn)
        out = taste_prioritized_kept(conn, user="nobody_tp@test.local", explore_frac=0.25)
        assert out == fifo                       # cold-start user -> identical FIFO
```

> Cold-start FIFO fallback is the safety-critical invariant. The full reorder is covered by Task 4's pure tests and the Task 6 live e2e (a hermetic reorder IT test would duplicate that).

- [ ] **Step 2: Run to verify it fails**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it PYTHONPATH=src .venv/bin/python -m pytest tests/db/test_taste_prioritized_kept.py -v`
Expected: FAIL (`cannot import name 'taste_prioritized_kept'`).

- [ ] **Step 3: Add the DB functions + wire the worker**

In `src/agf/db/candidates.py` (append; `from sqlalchemy import text` is already imported at module top — reuse it):

```python
def kept_with_embeddings(conn):
    """kept_advertiser_id -> candidate embedding (for kept candidates that were embedded)."""
    rows = conn.execute(text(
        "SELECT c.kept_advertiser_id, e.embedding "
        "FROM discovery.candidates c "
        "JOIN discovery.candidate_embeddings e ON e.candidate_id = c.id "
        "WHERE c.status='kept' AND c.kept_advertiser_id IS NOT NULL")).all()
    return {a: [float(x) for x in v] for a, v in rows}


def taste_prioritized_kept(conn, *, user, explore_frac=0.25):
    """kept_advertiser_ids reordered by cosine to `user`'s liked direction, with an
    explore reserve. Advisory: any cold-start / missing-data path returns plain FIFO."""
    from agf.discovery.triage_prior import liked_direction, prioritize
    fifo = kept_advertiser_ids(conn)
    if not fifo:
        return fifo
    emb = {a: [float(x) for x in v] for a, v in conn.execute(text(
        "SELECT advertiser_id, embedding FROM frontend.app_embeddings"))}
    labels = {a: r for a, r in conn.execute(text(
        "SELECT advertiser_id, rating FROM frontend.app_ratings WHERE user_email=:e"),
        {"e": user})}
    liked = [(a, "", emb[a]) for a, r in labels.items() if r == "like" and a in emb]
    disliked = [(a, "", emb[a]) for a, r in labels.items() if r == "dislike" and a in emb]
    direction = liked_direction(liked, disliked)
    if direction is None:
        return fifo
    cand_emb = kept_with_embeddings(conn)
    if not cand_emb:
        return fifo
    return prioritize(fifo, cand_emb, direction, explore_frac=explore_frac)
```

In `src/agf/workers/main.py`: add `taste_prioritized_kept` to the existing `from agf.db.candidates import (...)` block. Add a fetch+embed stage just before the enrich-selection block (after the scoring/preference stages, ~line 620), best-effort:

```python
    # 2c) candidate embeddings — fetch each kept candidate's store listing + embed it
    #     so the enrich budget can be taste-prioritized. Best-effort; never stalls.
    if os.environ.get("AGF_TRIAGE_PRIOR", "1") != "0" and not dry_run:
        try:
            from agf.discovery.candidate_embed import embed_candidates
            with engine.connect() as conn:
                cstats = embed_candidates(conn, itunes_client=http_client, commit_every=25)
                conn.commit()
            print(f"[worker] candidate embeddings: {cstats}", flush=True)
        except Exception as exc:
            print(f"[worker] candidate embed stage failed: {exc!r}",
                  file=sys.stderr, flush=True)
```

Replace `kept = kept_advertiser_ids(conn)` (~line 644) with:

```python
        if os.environ.get("AGF_TRIAGE_PRIOR", "1") != "0":
            try:
                kept = taste_prioritized_kept(
                    conn, user=os.environ.get("AGF_TRIAGE_USER", "founder@fielddesk.local"),
                    explore_frac=float(os.environ.get("AGF_TRIAGE_EXPLORE_FRAC", "0.25")))
            except Exception as exc:
                print(f"[worker] triage-prior fell back to FIFO: {exc!r}",
                      file=sys.stderr, flush=True)
                kept = kept_advertiser_ids(conn)
        else:
            kept = kept_advertiser_ids(conn)
```

> `http_client` is the worker's sweep-scoped httpx client (in scope in `run_once`); it's `None` in a dry run, which is why the embed stage is guarded by `not dry_run`.

- [ ] **Step 4: Run the test + worker import**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it PYTHONPATH=src .venv/bin/python -m pytest tests/db/test_taste_prioritized_kept.py -v` → PASS.
Run: `PYTHONPATH=src .venv/bin/python -c "import agf.workers.main"` → exit 0, no output.

- [ ] **Step 5: Full suite, then commit**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it PYTHONPATH=src .venv/bin/python -m pytest -q` → 0 failed (one pre-existing MinIO/S3 skip ok).

```bash
git add src/agf/db/candidates.py src/agf/workers/main.py tests/db/test_taste_prioritized_kept.py
git commit -m "feat(worker): taste-prioritized enrich budget (triage prior) with FIFO fallback"
```

---

### Task 6: Live verification (controller-run)

**Files:** none (operational). Uses the existing 800 kept candidates — does NOT need the worker sweep.

- [ ] **Step 1: Apply migration to prod**

```bash
PYTHONPATH=src AGF_DATABASE_URL=postgresql://agf:agf@localhost:5433/agf .venv/bin/python -c "from agf.db.engine import get_engine; from agf.db.migrate import apply_migrations; print(apply_migrations(get_engine()))"
```
Expected: includes `137_candidate_embeddings.sql`.

- [ ] **Step 2: Fetch + embed the existing kept candidates (real iTunes Lookup)**

```bash
PYTHONPATH=src AGF_DATABASE_URL=postgresql://agf:agf@localhost:5433/agf .venv/bin/python scripts/embed_candidates.py
```
Expected: `[embed_cand] {'embedded': N>0, 'fetched': M, 'pending': P}`. (Android candidates need the Node sidecar; iOS go via batch Lookup. If Gemini embed 429s, backoff paces it; re-run resumes — embed-once means it continues where it left off.)

- [ ] **Step 3: Confirm the reorder vs FIFO**

```bash
PYTHONPATH=src AGF_DATABASE_URL=postgresql://agf:agf@localhost:5433/agf .venv/bin/python -c "
from agf.db.engine import get_engine
from agf.db.candidates import taste_prioritized_kept, kept_advertiser_ids
with get_engine().connect() as c:
    fifo = kept_advertiser_ids(c)
    pri = taste_prioritized_kept(c, user='founder@fielddesk.local', explore_frac=0.25)
    print('len', len(pri), 'head changed:', fifo[:10] != pri[:10], 'same set:', set(fifo)==set(pri))
"
```
Expected: `head changed: True`, `same set: True` (taste reorders the head, drops nobody). If `head changed: False`, check kept candidates have embeddings (Step 2 count) and the founder has ≥8 likes among advertisers that also have `app_embeddings`.

- [ ] **Step 4: Restart the worker (load the new stages)**

```bash
launchctl kickstart -k gui/$(id -u)/com.fielddesk.worker
```
The fetch+embed + reorder now run each cycle. NEW candidates flowing in is gated on the separate worker-sweep-hang fix (memory `worker-hangs-in-sweep-2026-06-19`, parallel session) — but the prior already works on the existing kept set.

---

## Self-Review

**Spec coverage:** migration 137 → T1. direction accessor → T2. candidate store-fetch + embed (embed-once, unfetchable-skip, script) → T3. prioritize + explore reserve + liked_direction → T4. taste_prioritized_kept + worker fetch/embed stage + reorder + FIFO fallback + env knobs → T5. live e2e on existing kept → T6. Cold-start/error fallback → T4 (passthrough) + T5 (try/except). ✓ No `candidates.body_text` / sweep change (dropped per the fetch-listing decision). ✓

**Placeholder scan:** no TBD/TODO; every code step has complete code; commands show expected output.

**Type consistency:** `embed_candidates(conn,*,itunes_client,gplay_runner,lookup_itunes,lookup_play,embed_fn,commit_every,limit)`, `prioritize(fifo_order, emb_by_id, direction, explore_frac)`, `liked_direction(liked, disliked)`, `taste_prioritized_kept(conn,*,user,explore_frac)`, `kept_with_embeddings(conn)`, `ContentPreferenceModel.direction` — consistent across T2–T5. `AppMeta(track_id, name, seller, genre, release_date, rating_count, description=…)` matches `agf.enrich`. `lookup_itunes_batch(client, ids)` keys by str `track_id` = candidate `store_id`.
