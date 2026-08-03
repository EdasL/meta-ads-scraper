# Topic Diversity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Re-key the feed's diversity machinery (whitespace axis + review-queue per-niche cap) from discovery keywords onto embedding-cluster topics, and add redundancy-graded keyword retirement for both dynamic and static sweep pools.

**Architecture:** A new `frontend.app_topics` table (advertiser → k-means topic over the existing 768-d `frontend.app_embeddings`) is refreshed each compute cycle and consumed by (a) the Python whitespace axis and (b) the Go review-queue cap. Keyword redundancy uses the existing `candidate_embeddings.crowding` signal: a batch retire pass for `discovery.dynamic_keywords` and a `discovery.keyword_suppressions` table filtered out of static sweep entries, both run weekly inside the generic-deep stage.

**Tech Stack:** Python 3.12 + SQLAlchemy + numpy (no sklearn — pure-numpy k-means), Go (gorm), Postgres. Spec: `docs/superpowers/specs/2026-08-03-topic-diversity-design.md`.

## Global Constraints

- Test DB: `AGF_TEST_DB_URL=postgresql+psycopg://agf:agf@localhost:55433/agf_it`; ONE test consumer at a time; full suite must end 0 failed (a skip is not a pass).
- Run verification commands BARE — never piped through tail/grep in the same call as the verdict.
- Migration file number: **161** (160_saved_groups.sql is the latest; verify 161 is still free at execution time — a parallel session may take it; renumber to the next free if so).
- Follow `tests/discovery/test_crowding_refresh.py` / `tests/discovery/conftest.py` idioms for DB tests (engine fixture, only_ids isolation on shared test DB).
- Commit per task with pathspec limits (`git commit -- <paths>`); shared tree — re-read files before editing, stage explicit paths only.
- New env knobs: `AGF_TOPIC_K` (default 40), `AGF_KW_REDUNDANT_MIN_KEPT` (default 20), `AGF_KW_REDUNDANT_SHARE` (default 0.6). Existing: `AGF_CROWDING_TAIL_CANDIDATE` (0.90).
- Product nouns intact: Field Desk, GemScore, cohort. No UI copy changes in this increment.

---

### Task 1: Migration 161 — app_topics + keyword_suppressions

**Files:**
- Create: `db/schema/161_topics_suppressions.sql`
- Test: `tests/discovery/test_topic_schema.py`

**Interfaces:**
- Produces: tables `frontend.app_topics(advertiser_id PK, topic_id, topic_size, computed_at)` and `discovery.keyword_suppressions(keyword PK, reason, kept_n, crowded_share, suppressed_at)` used by Tasks 2, 3, 5, 6, 7.

- [ ] **Step 1: Write the failing test**

```python
"""Schema presence for migration 161 (topic diversity)."""
from sqlalchemy import text


def _cols(conn, schema, table):
    return {r.column_name for r in conn.execute(text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = :s AND table_name = :t"),
        {"s": schema, "t": table})}


def test_app_topics_table(engine):
    with engine.connect() as conn:
        assert _cols(conn, "frontend", "app_topics") >= {
            "advertiser_id", "topic_id", "topic_size", "computed_at"}


def test_keyword_suppressions_table(engine):
    with engine.connect() as conn:
        assert _cols(conn, "discovery", "keyword_suppressions") >= {
            "keyword", "reason", "kept_n", "crowded_share", "suppressed_at"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `AGF_TEST_DB_URL=postgresql+psycopg://agf:agf@localhost:55433/agf_it .venv/bin/pytest tests/discovery/test_topic_schema.py -v`
Expected: FAIL (empty column sets — tables missing).

- [ ] **Step 3: Write the migration**

```sql
-- 161: topic diversity (spec docs/superpowers/specs/2026-08-03-topic-diversity-design.md).
-- frontend.app_topics: per-advertiser embedding-cluster topic, recomputed each compute
-- cycle by src/agf/scoring/topics.py (k-means over frontend.app_embeddings). Consumed by
-- the whitespace axis (topic_size percentile) and the review-queue per-niche cap
-- ("t:<topic_id>"). Whole-corpus swap each refresh; no staleness bookkeeping.
CREATE TABLE IF NOT EXISTS frontend.app_topics (
  advertiser_id BIGINT PRIMARY KEY REFERENCES public.advertisers(id) ON DELETE CASCADE,
  topic_id      INTEGER NOT NULL,
  topic_size    INTEGER NOT NULL,
  computed_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- discovery.keyword_suppressions: static taxonomy keywords whose kept catch is
-- redundant (crowded share >= AGF_KW_REDUNDANT_SHARE with >= AGF_KW_REDUNDANT_MIN_KEPT
-- embedded kept candidates). Sweep entry builders filter these out (fail-open).
-- DELETE a row to un-suppress (founder override). Dynamic keywords are handled by
-- lifecycle retirement instead, never by this table.
CREATE TABLE IF NOT EXISTS discovery.keyword_suppressions (
  keyword       TEXT PRIMARY KEY,
  reason        TEXT NOT NULL,
  kept_n        INTEGER NOT NULL,
  crowded_share REAL NOT NULL,
  suppressed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

- [ ] **Step 4: Apply to the test DB and verify test passes**

Run: `.venv/bin/python scripts/apply_migrations.py` with `AGF_DATABASE_URL` set to the test DB URL (check the script's env convention first — mirror how existing tests get schema), then re-run Step 2's pytest.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add db/schema/161_topics_suppressions.sql tests/discovery/test_topic_schema.py
git commit -m "feat(diversity): migration 161 — app_topics + keyword_suppressions" -- db/schema/161_topics_suppressions.sql tests/discovery/test_topic_schema.py
```

---

### Task 2: Topic producer — pure k-means + refresh_topics

**Files:**
- Create: `src/agf/scoring/topics.py`
- Test: `tests/scoring/test_topics.py`

**Interfaces:**
- Consumes: `frontend.app_embeddings(advertiser_id, embedding, dim)`, `frontend.app_topics` (Task 1).
- Produces: `kmeans_assign(matrix, k, seed=0) -> np.ndarray[int]` (pure) and `refresh_topics(engine, *, k=None, only_ids=None) -> dict` (returns `{"rows": int, "k": int}`), used by Tasks 3 and 4.

- [ ] **Step 1: Write the failing tests**

```python
"""Topic clustering: pure k-means + the DB refresh (spec 2026-08-03)."""
import numpy as np
from sqlalchemy import text

from agf.scoring.topics import kmeans_assign, refresh_topics


def _blob(center, n, rng, dim=8):
    v = np.tile(np.asarray(center, dtype=np.float32), (n, 1))
    v += rng.normal(0, 0.01, size=(n, dim)).astype(np.float32)
    return v / np.linalg.norm(v, axis=1, keepdims=True)


def test_kmeans_separates_two_obvious_blobs():
    rng = np.random.default_rng(7)
    a = _blob([1, 0, 0, 0, 0, 0, 0, 0], 20, rng)
    b = _blob([0, 1, 0, 0, 0, 0, 0, 0], 20, rng)
    labels = kmeans_assign(np.vstack([a, b]), k=2)
    assert len(set(labels[:20])) == 1
    assert len(set(labels[20:])) == 1
    assert labels[0] != labels[20]


def test_kmeans_k_capped_at_rows():
    rng = np.random.default_rng(7)
    labels = kmeans_assign(_blob([1, 0, 0, 0, 0, 0, 0, 0], 3, rng), k=40)
    assert len(labels) == 3


def test_refresh_topics_writes_rows_and_sizes(engine, seeded_advertisers):
    # seeded_advertisers: fixture inserting 4 advertisers with app_embeddings rows —
    # two near [1,0,...], two near [0,1,...] (write it in this test file's conftest
    # style, following tests/discovery/test_crowding_refresh.py's seeding pattern,
    # dim=8 embeddings are fine: refresh reads whatever dim the corpus has).
    ids = seeded_advertisers
    out = refresh_topics(engine, k=2, only_ids=ids)
    assert out == {"rows": 4, "k": 2}
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT advertiser_id, topic_id, topic_size FROM frontend.app_topics "
            "WHERE advertiser_id = ANY(:ids) ORDER BY advertiser_id"),
            {"ids": ids}).all()
    assert len(rows) == 4
    by_topic = {}
    for r in rows:
        by_topic.setdefault(r.topic_id, []).append(r)
    assert sorted(len(v) for v in by_topic.values()) == [2, 2]
    assert all(r.topic_size == len(by_topic[r.topic_id]) for r in rows)


def test_refresh_topics_empty_corpus_noop(engine):
    assert refresh_topics(engine, only_ids=[-1]) == {"rows": 0, "k": 0}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `AGF_TEST_DB_URL=... .venv/bin/pytest tests/scoring/test_topics.py -v`
Expected: FAIL with ModuleNotFoundError / missing fixture.

- [ ] **Step 3: Implement**

```python
"""Embedding-cluster topics: k-means over frontend.app_embeddings, stored in
frontend.app_topics (spec 2026-08-03). The topic is the DIVERSITY key — it replaces
discovery-keyword "niches" for the whitespace axis and the review-queue cap, so 294
near-identical AI photo editors share ONE topic no matter which keyword found them.
Pure-numpy k-means++ (no sklearn in the venv); embeddings are L2-normalized so
euclidean k-means ≈ cosine. Assignments are recomputed whole-corpus each cycle —
consumers use per-run percentiles / per-request cap keys, so run-to-run label drift
is harmless. Follows crowding_refresh.py's engine-not-conn / no-txn-over-compute
pattern and its only_ids test-isolation convention."""
from __future__ import annotations

import os
from collections import Counter

import numpy as np
from sqlalchemy import text


def topic_k() -> int:
    try:
        return int(os.environ.get("AGF_TOPIC_K", "40"))
    except ValueError:
        return 40


def kmeans_assign(matrix: np.ndarray, k: int, seed: int = 0) -> np.ndarray:
    """k-means++ init + Lloyd iterations, returns int labels. k is capped at
    n_rows; input rows should be unit-norm (cosine ≡ euclidean up to scale)."""
    m = np.asarray(matrix, dtype=np.float32)
    n = m.shape[0]
    k = max(1, min(k, n))
    rng = np.random.default_rng(seed)
    # k-means++ seeding
    centers = [m[rng.integers(n)]]
    for _ in range(1, k):
        d2 = np.min(
            ((m[:, None, :] - np.asarray(centers)[None, :, :]) ** 2).sum(-1), axis=1)
        total = float(d2.sum())
        if total <= 0:
            centers.append(m[rng.integers(n)])
            continue
        centers.append(m[rng.choice(n, p=d2 / total)])
    c = np.asarray(centers, dtype=np.float32)
    labels = np.zeros(n, dtype=np.int64)
    for _ in range(50):
        d = ((m[:, None, :] - c[None, :, :]) ** 2).sum(-1)
        new_labels = d.argmin(axis=1)
        if np.array_equal(new_labels, labels) and _ > 0:
            break
        labels = new_labels
        for j in range(k):
            members = m[labels == j]
            if len(members):
                c[j] = members.mean(axis=0)
    return labels


def refresh_topics(engine, *, k: int | None = None, only_ids=None) -> dict:
    """Recompute every advertiser's topic and swap frontend.app_topics in one txn.
    Engine (not conn): corpus read on a short-lived connection, numpy compute holds
    no txn, one write txn at the end (crowding_refresh pattern). only_ids scopes
    both the corpus and the delete — the shared-test-DB isolation convention."""
    if k is None:
        k = topic_k()
    sql = ("SELECT advertiser_id, embedding FROM frontend.app_embeddings "
           "WHERE dim > 0")
    params = {}
    if only_ids is not None:
        sql += " AND advertiser_id = ANY(:ids)"
        params = {"ids": list(only_ids)}
    sql += " ORDER BY advertiser_id"
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).all()
    if not rows:
        return {"rows": 0, "k": 0}

    keys = [r[0] for r in rows]
    vecs = [list(r[1]) for r in rows]
    dim = Counter(len(v) for v in vecs).most_common(1)[0][0]
    kept = [(key, v) for key, v in zip(keys, vecs) if len(v) == dim]
    keys = [key for key, _ in kept]
    matrix = np.asarray([v for _, v in kept], dtype=np.float32)

    labels = kmeans_assign(matrix, k=k)
    sizes = Counter(int(l) for l in labels)
    payload = [{"a": key, "t": int(l), "s": sizes[int(l)]}
               for key, l in zip(keys, labels)]
    with engine.begin() as conn:
        if only_ids is not None:
            conn.execute(text(
                "DELETE FROM frontend.app_topics WHERE advertiser_id = ANY(:ids)"),
                {"ids": list(only_ids)})
        else:
            conn.execute(text("DELETE FROM frontend.app_topics"))
        conn.execute(text(
            "INSERT INTO frontend.app_topics (advertiser_id, topic_id, topic_size) "
            "VALUES (:a, :t, :s)"), payload)
    return {"rows": len(payload), "k": int(len(sizes))}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `AGF_TEST_DB_URL=... .venv/bin/pytest tests/scoring/test_topics.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/agf/scoring/topics.py tests/scoring/test_topics.py
git commit -m "feat(diversity): topic producer — numpy k-means + refresh_topics" -- src/agf/scoring/topics.py tests/scoring/test_topics.py
```

---

### Task 3: Whitespace axis re-key onto topics

**Files:**
- Modify: `src/agf/scoring/whitespace.py` (add `load_topic_crowding`; docstring: keyword niches are display-only now)
- Modify: `src/agf/scoring/run_scoring.py:106-119` (W axis from topics; keyword niche kept ONLY for the `niche_keyword`/`niche_advertiser_count` display columns)
- Test: `tests/scoring/test_whitespace.py` (extend), the run_scoring test file that exercises whitespace (find it via `grep -rn "load_niche_crowding\|whitespace" tests/`)

**Interfaces:**
- Consumes: `frontend.app_topics` (Tasks 1-2).
- Produces: `load_topic_crowding(conn) -> tuple[dict[int, tuple[int, int]], list[int]]` — `{advertiser_id: (topic_id, topic_size)}` plus sorted distinct topic sizes (percentile population).

- [ ] **Step 1: Write the failing test**

```python
def test_load_topic_crowding_maps_and_sizes(engine, seeded_topics):
    # seeded_topics fixture: insert advertisers A,B,C with app_topics rows —
    # A,B in topic 1 (size 2), C in topic 7 (size 1).
    a, b, c = seeded_topics
    from agf.scoring.whitespace import load_topic_crowding
    with engine.connect() as conn:
        by_adv, sizes = load_topic_crowding(conn)
    assert by_adv[a] == (1, 2) and by_adv[b] == (1, 2) and by_adv[c] == (7, 1)
    assert sizes == sorted(sizes) and {1, 2} <= set(sizes)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `AGF_TEST_DB_URL=... .venv/bin/pytest tests/scoring/test_whitespace.py -v`
Expected: FAIL with ImportError (`load_topic_crowding` missing).

- [ ] **Step 3: Implement**

In `whitespace.py` add (keep `load_niche_crowding` unchanged; extend the module docstring to say the keyword-niche map now feeds display columns only):

```python
def load_topic_crowding(conn):
    """Returns (by_advertiser, topic_sizes) from frontend.app_topics.

    by_advertiser: {advertiser_id: (topic_id, topic_size)}.
    topic_sizes: sorted DISTINCT-topic sizes — the percentile population for the
    whitespace axis. An advertiser without a topic row (no embedding yet) is
    simply absent; the axis floors to neutral, same as missing keyword lineage."""
    rows = conn.execute(text(
        "SELECT advertiser_id, topic_id, topic_size FROM frontend.app_topics")).all()
    by_adv = {int(r.advertiser_id): (int(r.topic_id), int(r.topic_size)) for r in rows}
    sizes = sorted({(int(r.topic_id)): int(r.topic_size) for r in rows}.values())
    return by_adv, sizes
```

In `run_scoring.py`, replace the axis wiring (keep the display columns from the old map):

```python
    # niche crowding: TOPIC-based for the whitespace axis (embedding clusters,
    # spec 2026-08-03); the keyword-niche map remains ONLY to fill the
    # niche_keyword/niche_advertiser_count display columns.
    niche_by_aid, _ = load_niche_crowding(conn)
    topic_by_aid, topic_sizes = load_topic_crowding(conn)
```

and in the loop:

```python
            niche = niche_by_aid.get(fv.advertiser_id)
            topic = topic_by_aid.get(fv.advertiser_id)
            w = whitespace({"crowding": None if topic is None
                            else 1.0 - pctile(topic[1], topic_sizes)})
```

(`:nk`/`:nc` INSERT params stay bound to `niche` exactly as today — display only.)

- [ ] **Step 4: Run the scoring test files bare**

Run: `AGF_TEST_DB_URL=... .venv/bin/pytest tests/scoring -v`
Expected: PASS, 0 failed. Any pre-existing scoring test that asserted keyword-derived whitespace values must be updated to seed `frontend.app_topics` instead — that IS part of this task.

- [ ] **Step 5: Commit**

```bash
git add src/agf/scoring/whitespace.py src/agf/scoring/run_scoring.py tests/scoring/test_whitespace.py
git commit -m "feat(diversity): whitespace axis re-keyed onto embedding topics" -- src/agf/scoring/whitespace.py src/agf/scoring/run_scoring.py tests/scoring
```

---

### Task 4: Wire topics into compute_cycle + run_stage

**Files:**
- Modify: `src/agf/workers/compute_cycle.py` (after the app-embed/crowding step)
- Modify: `src/agf/workers/run_stage.py` (new `topics` stage)
- Test: the existing compute-cycle test file (`grep -rln "run_compute_cycle" tests/`), plus a run_stage smoke if that file has one per stage.

**Interfaces:**
- Consumes: `refresh_topics(engine, k=None, only_ids=None)` (Task 2).
- Produces: compute-cycle out dict gains `"topics"`; CLI `python -m agf.workers.run_stage topics`.

- [ ] **Step 1: Write the failing test** — in the compute-cycle test file, follow its existing monkeypatch style; assert that when the embed step reports added rows, `refresh_topics` is called and its result lands in `out["topics"]`, and that on a no-op embed cycle with a NON-EMPTY `frontend.app_topics` it is skipped (the crowding-skip guard idiom), but runs when the table is EMPTY (cold-start backfill).

```python
def test_compute_cycle_refreshes_topics_after_embeds(engine, monkeypatch):
    calls = []
    monkeypatch.setattr("agf.workers.compute_cycle.refresh_topics",
                        lambda eng, **kw: calls.append(kw) or {"rows": 5, "k": 2})
    monkeypatch.setattr("scripts.embed_apps.embed_apps",
                        lambda conn, commit_every=25: {"embedded": 3})
    # ... existing fixture pattern for engine/http_client/as_of ...
    out = run_compute_cycle(engine, None, date(2026, 8, 3))
    assert out["topics"] == {"rows": 5, "k": 2}
```

- [ ] **Step 2: Run it, verify FAIL** (`out` has no `"topics"` key).

- [ ] **Step 3: Implement** — in `compute_cycle.py` step 2, after the crowding recompute:

```python
from agf.scoring.topics import refresh_topics
...
            out["embed"] = emb
            if _added(emb):  # skip the full recompute on a no-op cycle
                out["embed_crowding"] = refresh_crowding(
                    engine, table="frontend.app_embeddings")
            # topics ride the same signal; also run when the table is empty
            # (cold-start backfill after migration 161).
            try:
                if _added(emb) or _topics_empty(engine):
                    out["topics"] = refresh_topics(engine)
            except Exception as exc:
                print(f"[compute] topic refresh failed: {exc!r}",
                      file=sys.stderr, flush=True)
```

with:

```python
def _topics_empty(engine) -> bool:
    from sqlalchemy import text
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT NOT EXISTS (SELECT 1 FROM frontend.app_topics)")
        ).scalar_one()
```

In `run_stage.py`, add a stage mirroring `_stage_compute`'s shape:

```python
def _stage_topics(engine, args):
    from agf.scoring.topics import refresh_topics
    return refresh_topics(engine)
```

register `"topics": _stage_topics` in `STAGES` and `_add("topics")` in the parser block.

- [ ] **Step 4: Run the workers test files bare** — `AGF_TEST_DB_URL=... .venv/bin/pytest tests/workers -v` → PASS 0 failed.

- [ ] **Step 5: Commit**

```bash
git add src/agf/workers/compute_cycle.py src/agf/workers/run_stage.py tests/workers
git commit -m "feat(diversity): topics refresh in compute cycle + run_stage topics" -- src/agf/workers/compute_cycle.py src/agf/workers/run_stage.py tests/workers
```

---

### Task 5: Go review-queue cap re-key

**Files:**
- Modify: `backend/models/models.go` (ShortlistRow: add TopicID)
- Modify: `backend/repositories/preference_repo.go:101-110` (queue query joins app_topics)
- Modify: `backend/repositories/preference_queue.go:79-90` (`nicheKey` topic-first)
- Test: `backend/repositories/preference_queue_test.go`

**Interfaces:**
- Consumes: `frontend.app_topics` (Task 1).
- Produces: `ShortlistRow.TopicID *int` (json `topic_id,omitempty`, gorm column `topic_id`); cap key `"t:<topic_id>"`.

- [ ] **Step 1: Write the failing test** — in `preference_queue_test.go`, following its existing pure-assembleQueue test style:

```go
func TestNicheKeyPrefersTopic(t *testing.T) {
	topic := 7
	kw := "on the app store"
	c := prefCandidate{}
	c.TopicID = &topic
	c.NicheKeyword = &kw
	if got := nicheKey(c); got != "t:7" {
		t.Fatalf("nicheKey with topic = %q, want t:7", got)
	}
	c.TopicID = nil
	if got := nicheKey(c); got != "n:on the app store" {
		t.Fatalf("nicheKey fallback = %q, want n:on the app store", got)
	}
}

func TestPerTopicCapAppliesAcrossDifferentKeywords(t *testing.T) {
	// 6 candidates in ONE topic but with 6 DIFFERENT niche_keywords (the broken
	// pre-topic state) must be capped at defaultPerNicheCap by topic.
	topic := 3
	cands := make([]prefCandidate, 6)
	for i := range cands {
		kw := fmt.Sprintf("keyword-%d", i)
		cands[i].AdvertiserID = i + 1
		cands[i].TopicID = &topic
		cands[i].NicheKeyword = &kw
		cands[i].RankScore = 0.9
		cands[i].Source = "scored"
	}
	got := assembleQueue(cands, queueOpts{Limit: 6, PerNicheCap: 3,
		ExploitFrac: 1.0, Rng: rand.New(rand.NewSource(1))})
	if len(got) != 3 {
		t.Fatalf("per-topic cap: got %d cards, want 3", len(got))
	}
}
```

(Adjust field access to the real embedded-struct paths — `AdvertiserID` lives on ShortlistRow; mirror how existing tests in that file construct prefCandidate.)

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && go test ./repositories/ -run "TestNicheKey|TestPerTopicCap" -v`
Expected: FAIL to compile (`c.TopicID` undefined) — that is the red state.

- [ ] **Step 3: Implement**

`models.go` — inside ShortlistRow, next to NicheKeyword's block:

```go
	// Embedding-cluster topic (161_topics_suppressions.sql), joined from
	// frontend.app_topics by the review-queue query only; other ShortlistRow
	// queries don't select it and leave it nil. Diversity-cap key, not UI data.
	TopicID *int `json:"topic_id,omitempty" gorm:"column:topic_id"`
```

`preference_queue.go`:

```go
// nicheKey is the diversity-cap key: embedding topic, else niche_keyword, else
// cohort, else exempt. Topic first: keyword "niches" scatter clone clusters
// across dozens of keys (spec 2026-08-03).
func nicheKey(c prefCandidate) string {
	if c.TopicID != nil {
		return "t:" + strconv.Itoa(*c.TopicID)
	}
	if c.NicheKeyword != nil && *c.NicheKeyword != "" {
		return "n:" + *c.NicheKeyword
	}
	if c.Cohort != "" {
		return "c:" + c.Cohort
	}
	return "" // empty key is exempt from the cap (unknown niche)
}
```

(add `strconv` to imports.)

`preference_repo.go` — wrap the candidate query in a subselect so unqualified mvCols stay unambiguous and NOT EXISTS correlation is untouched:

```go
		q := `SELECT s.*, t.topic_id
		      FROM (SELECT ` + mvCols + `
		            FROM frontend.emerging_scaling_apps
		            WHERE NOT EXISTS (
		              SELECT 1 FROM frontend.app_ratings r
		              WHERE r.user_email = ?
		                AND r.advertiser_id = frontend.emerging_scaling_apps.advertiser_id)`
		args := []interface{}{userEmail}
		if len(exclude) > 0 {
			q += ` AND frontend.emerging_scaling_apps.advertiser_id NOT IN (?)`
			args = append(args, exclude)
		}
		q += `) s LEFT JOIN frontend.app_topics t ON t.advertiser_id = s.advertiser_id`
```

(mvScanRow embeds ShortlistRow, so `topic_id` scans into the new field with no further change; verify `toShortlistRows` copies the embedded struct wholesale — it does, it passes `row.ShortlistRow` through.)

- [ ] **Step 4: Run the Go suite bare**

Run: `cd backend && go test ./...`
Expected: ok everywhere, 0 failures.

- [ ] **Step 5: Commit**

```bash
git add backend/models/models.go backend/repositories/preference_repo.go backend/repositories/preference_queue.go backend/repositories/preference_queue_test.go
git commit -m "feat(diversity): review-queue cap keys on embedding topic" -- backend/models/models.go backend/repositories/preference_repo.go backend/repositories/preference_queue.go backend/repositories/preference_queue_test.go
```

---

### Task 6: Redundancy grading — dynamic retire pass + static suppressions

**Files:**
- Modify: `src/agf/db/dynamic_keywords.py` (add `retire_redundant_keywords`, env knobs)
- Create: `src/agf/db/keyword_suppressions.py`
- Test: `tests/discovery/test_keyword_redundancy_retirement.py`

**Interfaces:**
- Consumes: `discovery.candidates`, `discovery.candidate_embeddings.crowding`, `discovery.dynamic_keywords`, `discovery.keyword_suppressions` (Task 1); `crowding_tail()` (existing).
- Produces: `retire_redundant_keywords(conn) -> list[str]` (retired keywords); `suppress_redundant_static_keywords(conn, keywords) -> list[str]` (newly suppressed); `load_suppressed_keywords(conn) -> set[str]`. Env: `AGF_KW_REDUNDANT_MIN_KEPT` (20), `AGF_KW_REDUNDANT_SHARE` (0.6).

- [ ] **Step 1: Write the failing tests** — seed pattern per test (follow the conftest seeding idiom): one dynamic keyword `status='active'` with 25 kept candidates whose embeddings have `crowding=0.95` (share 1.0 → retire); one with 25 kept at `crowding=0.10` (share 0 → survives); one with only 5 kept at 0.95 (below min_kept → survives). Same three shapes for the static pass with plain source_keywords, asserting rows land in `keyword_suppressions` with reason `'redundant_catch'` and that `load_suppressed_keywords` returns them. Also: re-running the static pass twice is idempotent (upsert), and a keyword whose candidates have NULL crowding is NOT suppressed (degrade open).

```python
def test_retire_redundant_dynamic_keyword(engine, seed_kw):
    kid = seed_kw(keyword="clone magnet", status="active", kept=25, crowding=0.95)
    seed_kw(keyword="fresh finder", status="active", kept=25, crowding=0.10)
    seed_kw(keyword="tiny sample", status="active", kept=5, crowding=0.95)
    from agf.db.dynamic_keywords import retire_redundant_keywords
    with engine.begin() as conn:
        retired = retire_redundant_keywords(conn)
    assert retired == ["clone magnet"]
    with engine.connect() as conn:
        st = conn.execute(text("SELECT keyword, status FROM discovery.dynamic_keywords "
                               "WHERE keyword IN ('clone magnet','fresh finder','tiny sample')")).all()
    assert dict(st) == {"clone magnet": "retired", "fresh finder": "active",
                        "tiny sample": "active"}
```

- [ ] **Step 2: Run to verify FAIL** (ImportError).

- [ ] **Step 3: Implement**

`dynamic_keywords.py` additions:

```python
def redundant_min_kept() -> int:
    # A keyword needs this many EMBEDDED kept candidates before the redundancy
    # grade is trustworthy — protects low-volume keywords from noisy shares.
    return _env_int("AGF_KW_REDUNDANT_MIN_KEPT", 20)


def redundant_share() -> float:
    # Crowded share (kept candidates with crowding >= crowding_tail()) at or
    # above which a keyword's catch counts as redundant. 0.6 = today's victims
    # are unambiguous clone-cluster magnets (measured 2026-08-03).
    return _env_float("AGF_KW_REDUNDANT_SHARE", 0.6)


def retire_redundant_keywords(conn) -> list[str]:
    """Retire active/resting keywords whose kept catch is mostly clone-cluster
    (crowded share >= redundant_share() over >= redundant_min_kept() embedded kept
    candidates). NULL crowding rows count in the denominator only — degrade open.
    Trials are untouched (the promotion verdict already grades them). Returns the
    retired keywords. Spec 2026-08-03; complements (never replaces) the dry-streak
    resting lifecycle."""
    rows = conn.execute(text(
        "WITH g AS ("
        "  SELECT k.id, count(e.candidate_id) AS kept, "
        "         count(*) FILTER (WHERE e.crowding >= :tail) AS crowded "
        "  FROM discovery.dynamic_keywords k "
        "  JOIN discovery.candidates c ON c.source_keyword = k.keyword "
        "    AND c.status = 'kept' "
        "  JOIN discovery.candidate_embeddings e ON e.candidate_id = c.id "
        "  WHERE k.status IN ('active','resting') "
        "  GROUP BY k.id "
        "  HAVING count(e.candidate_id) >= :min_kept "
        "     AND count(*) FILTER (WHERE e.crowding >= :tail)::numeric "
        "         / count(e.candidate_id) >= :share) "
        "UPDATE discovery.dynamic_keywords k SET status = 'retired' "
        "FROM g WHERE k.id = g.id RETURNING k.keyword"),
        {"tail": crowding_tail(), "min_kept": redundant_min_kept(),
         "share": redundant_share()}).all()
    return sorted(r.keyword for r in rows)
```

`keyword_suppressions.py`:

```python
"""Static-keyword suppression on redundancy grounds (spec 2026-08-03). The static
taxonomy lists stay founder-approved code; this table is the data-driven overlay
that pulls a mined-out phrase from rotation. DELETE a row to un-suppress. Sweep
entry builders read load_suppressed_keywords FAIL-OPEN — suppression is an
optimization and must never block a sweep. Measured 2026-08-03: high-crowding
victims are "app", "ai", "try the app free", "install the app"; high-exclusive-
yield abstract phrases ("on the app store", "get the app") stay — the sweep is
taste-blind by standing decision 2026-06-19."""
import sys

from sqlalchemy import text

from agf.db.dynamic_keywords import (crowding_tail, redundant_min_kept,
                                     redundant_share)


def load_suppressed_keywords(conn) -> set[str]:
    return {r.keyword for r in conn.execute(
        text("SELECT keyword FROM discovery.keyword_suppressions"))}


def drop_suppressed_entries(engine, entries):
    """Filter TaxonomyEntry lists through the suppression table, fail-open."""
    try:
        with engine.connect() as conn:
            suppressed = load_suppressed_keywords(conn)
    except Exception as exc:
        print(f"[suppress] read failed (fail-open): {exc!r}", file=sys.stderr,
              flush=True)
        return list(entries)
    return [e for e in entries if e.keyword not in suppressed]


def suppress_redundant_static_keywords(conn, keywords) -> list[str]:
    """Grade each static keyword's kept catch; upsert redundant ones. Same rule
    as the dynamic pass; idempotent (ON CONFLICT refreshes the metrics). Returns
    the keywords suppressed by THIS call that were not suppressed before."""
    keywords = list(keywords)
    if not keywords:
        return []
    already = load_suppressed_keywords(conn)
    rows = conn.execute(text(
        "SELECT c.source_keyword AS kw, count(e.candidate_id) AS kept, "
        "       count(*) FILTER (WHERE e.crowding >= :tail) AS crowded "
        "FROM discovery.candidates c "
        "JOIN discovery.candidate_embeddings e ON e.candidate_id = c.id "
        "WHERE c.status = 'kept' AND c.source_keyword = ANY(:kws) "
        "GROUP BY 1 "
        "HAVING count(e.candidate_id) >= :min_kept "
        "   AND count(*) FILTER (WHERE e.crowding >= :tail)::numeric "
        "       / count(e.candidate_id) >= :share"),
        {"tail": crowding_tail(), "min_kept": redundant_min_kept(),
         "share": redundant_share(), "kws": keywords}).all()
    fresh = []
    for r in rows:
        conn.execute(text(
            "INSERT INTO discovery.keyword_suppressions "
            "(keyword, reason, kept_n, crowded_share) "
            "VALUES (:kw, 'redundant_catch', :n, :s) "
            "ON CONFLICT (keyword) DO UPDATE SET kept_n = EXCLUDED.kept_n, "
            "  crowded_share = EXCLUDED.crowded_share, suppressed_at = now()"),
            {"kw": r.kw, "n": int(r.kept),
             "s": float(r.crowded) / float(r.kept)})
        if r.kw not in already:
            fresh.append(r.kw)
    return sorted(fresh)
```

- [ ] **Step 4: Run bare** — `AGF_TEST_DB_URL=... .venv/bin/pytest tests/discovery/test_keyword_redundancy_retirement.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agf/db/dynamic_keywords.py src/agf/db/keyword_suppressions.py tests/discovery/test_keyword_redundancy_retirement.py
git commit -m "feat(diversity): redundancy-graded keyword retirement + static suppressions" -- src/agf/db/dynamic_keywords.py src/agf/db/keyword_suppressions.py tests/discovery/test_keyword_redundancy_retirement.py
```

---

### Task 7: Enforcement — sweep entry filter + weekly grading cadence

**Files:**
- Modify: `src/agf/discovery/sweep.py:233,393` (default-entry branches filter suppressed)
- Modify: `src/agf/discovery/generic_deep.py` (filter its entries; run both grading passes at stage start)
- Test: `tests/discovery/test_keyword_suppression_enforcement.py` (+ extend the existing generic_deep test file)

**Interfaces:**
- Consumes: `drop_suppressed_entries(engine, entries)`, `suppress_redundant_static_keywords(conn, keywords)` (Task 6), `retire_redundant_keywords(conn)` (Task 6), `NICHE_TERMS + STORE_FRAGMENTS + GENERIC_DEEP_TERMS` (taxonomy).
- Produces: `run_generic_deep_stage` return dict gains `"retired_redundant": list[str]` and `"suppressed": list[str]`.

- [ ] **Step 1: Write the failing tests**

```python
def test_run_sweep_default_entries_skip_suppressed(engine, fake_extractor, suppress):
    # suppress fixture inserts ('app', 'redundant_catch', 25, 0.8) into
    # discovery.keyword_suppressions. fake_extractor records keywords searched
    # (reuse the existing sweep-test fake from this directory's sweep tests).
    suppress("app")
    asyncio.run(run_sweep(engine, fake_extractor, max_pages=1, kw_delay=0))
    assert "app" not in fake_extractor.searched_keywords


def test_generic_deep_stage_runs_grading_and_filters(engine, fake_extractor, suppress):
    suppress("get the app")
    out = run_generic_deep_stage(engine, fake_extractor, deep_pages=1, kw_delay=0)
    assert "get the app" not in fake_extractor.searched_keywords
    assert "retired_redundant" in out and "suppressed" in out
```

- [ ] **Step 2: Run to verify FAIL.**

- [ ] **Step 3: Implement**

`sweep.py` — both default branches (`run_sweep` line ~233, `run_parallel_sweep` line ~393) become:

```python
    if entries is None:
        from agf.db.keyword_suppressions import drop_suppressed_entries
        entries = drop_suppressed_entries(engine, sweep_entries())
```

(import inside the branch mirrors the repo's circular-import-avoidance idiom; both functions have `engine` in scope.)

`generic_deep.py` — at the top of `run_generic_deep_stage`, before the sweep:

```python
    from agf.db.dynamic_keywords import retire_redundant_keywords
    from agf.db.keyword_suppressions import (drop_suppressed_entries,
                                             suppress_redundant_static_keywords)
    from agf.discovery.taxonomy import NICHE_TERMS, STORE_FRAGMENTS

    # Weekly redundancy grading (spec 2026-08-03): retire clone-magnet dynamic
    # keywords, refresh static suppressions, THEN build this pass's entries.
    retired_redundant, suppressed = [], []
    try:
        with begin(engine) as conn:
            retired_redundant = retire_redundant_keywords(conn)
            suppressed = suppress_redundant_static_keywords(
                conn, NICHE_TERMS + STORE_FRAGMENTS + GENERIC_DEEP_TERMS)
    except Exception as exc:
        print(f"[worker] redundancy grading failed (continuing): {exc!r}",
              file=sys.stderr, flush=True)
    if entries is None:
        entries = generic_deep_entries(deep_pages)
    entries = drop_suppressed_entries(engine, entries)
```

and add both lists to the return dict:

```python
    return {"sweep": sweep, "mined": list(mined), "trials_added": trials_added,
            "copy_rows": len(copy_rows), "retired_redundant": retired_redundant,
            "suppressed": suppressed}
```

- [ ] **Step 4: Run the discovery test dir bare** — `AGF_TEST_DB_URL=... .venv/bin/pytest tests/discovery -v` → PASS 0 failed.

- [ ] **Step 5: Commit**

```bash
git add src/agf/discovery/sweep.py src/agf/discovery/generic_deep.py tests/discovery
git commit -m "feat(diversity): suppression enforcement in sweeps + weekly grading cadence" -- src/agf/discovery/sweep.py src/agf/discovery/generic_deep.py tests/discovery
```

---

### Task 8 (controller, not a subagent task): whole-branch review + live verify

- [ ] Full Python suite bare, solo on the test DB: `AGF_TEST_DB_URL=... .venv/bin/pytest` → 0 failed. Full Go suite bare: `cd backend && go test ./...`.
- [ ] Whole-branch opus review (`scripts/review-package MERGE_BASE HEAD`), composition questions: does every new function have a live caller? do the cap fallback chain and the fail-open paths compose? did any task absorb foreign WIP from the shared tree?
- [ ] Live e2e on prod (in order): apply migration 161 → `run_stage topics` → probe `SELECT count(*), count(DISTINCT topic_id) FROM frontend.app_topics` → `run_scoring` smoke on a few ids → whitespace populated → deploy Go API → queue API probe: one batch has ≤3 cards per topic_id → grading passes via the generic_deep weekly stage (or one-off): report before/after keyword counts and the suppressions written (expect ~"app", "ai", "try the app free", "install the app"; expect "on the app store" NOT suppressed) → recreate worker containers (env changes need recreation, not restart).
- [ ] Update memory (`corpus-diversity-analysis-2026-08-03.md` → SHIPPED status + live numbers).
