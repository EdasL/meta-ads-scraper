# Content-based preference + keyword mining — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the 6-axis Logit serving model with a content-embedding similarity-to-likes ranker, and mine taste-aligned seed keywords from liked apps' copy behind a human gate.

**Architecture:** Gemini embeds each app's content (name + tagline + category + description + top ad copy) at 768-dim → `frontend.app_embeddings`. A per-user direction vector `mean(liked) − mean(disliked)` cosine-ranks all apps into `frontend.preference_scores` (same table + Go endpoint as Spec 1 — no API/web change). A cheap Gemini model proposes search phrases from liked apps into `discovery.dynamic_keywords status='pending'`, promoted to `trial` only by a human review script.

**Tech Stack:** Python 3.13, SQLAlchemy (psycopg v3), numpy, Gemini REST (`gemini-embedding-001` for vectors, a cheap flash-lite model for mining) via `urllib` — no new pip deps. Postgres (`db/schema/*.sql` migrations). statsmodels retained only for the off-serving axis analysis.

## Global Constraints

- All work on `main`; commit per task with **explicit paths** (never `git add -A`).
- Python DB/IT tests **skip silently** without `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it` — a skip is NOT a pass. Run the FULL suite and drive to **0 failed** before "done".
- Prod DB env var: `AGF_DATABASE_URL=postgresql://agf:agf@localhost:5433/agf`. Migrations applied via `agf.db.migrate.apply_migrations` (reads `db/schema/*.sql` in lexical order, idempotent ledger).
- Embeddings: Gemini `gemini-embedding-001`, request body `outputDimensionality: 768`, **L2-renormalize** every returned vector (raw output is ~0.59 norm — verified). Reuse `GEMINI_API_KEY` (already in shell; works for embeddings — verified live).
- Keyword mining uses a **cheap** Gemini model (flash-lite tier), not a powerful one — easy extraction task (user directive).
- Product copy is plain, clear, professional (CLAUDE.md): `why`-strings and any script output stay short and honest.
- Worker holds code in memory — after changes: `launchctl kickstart -k gui/$(id -u)/com.fielddesk.worker`.

## File Structure

| File | Responsibility |
|---|---|
| `db/schema/135_app_embeddings.sql` | `frontend.app_embeddings` table |
| `db/schema/136_dynamic_keywords_taste.sql` | add `rationale`,`inspired_by` cols + extend `status` check with `pending` |
| `src/agf/scoring/embeddings.py` | pure embed-text builder + hash + L2-norm; Gemini `embed_texts` network call |
| `scripts/embed_apps.py` | DB: incremental embed of scored apps → `app_embeddings` |
| `src/agf/scoring/content_preference.py` | pure: direction vector, cosine score, Platt calibration, nearest-liked `why`, cold-start |
| `scripts/score_content_preferences.py` | DB: load embeddings+labels → write `preference_scores` (`content-sim-v1`) |
| `scripts/analyze_axis_coefficients.py` | the retained 6-axis Logit, coefficients-only (off serving) |
| `src/agf/discovery/liked_keyword_mining.py` | pure dedup + response parse; cheap-Gemini `propose_phrases` |
| `scripts/mine_liked_keywords.py` | DB: liked content → `dynamic_keywords status='pending'` |
| `scripts/review_mined_keywords.py` | human gate: list `pending`, promote to `trial` |
| `src/agf/workers/main.py` | repoint preference stage to content scorer; add embed stage (`AGF_EMBED_APPS`) |

**Grounded schema facts (verified live):**
- Matview `frontend.emerging_scaling_apps` cols include: `advertiser_id, app_id, app_name, tagline, niche_keyword, funnel_url, gem_score, …` (no `description`/`genre`).
- `public.apps(id, name, genre, description, …)`; matview `app_id` = `apps.id`.
- `public.ads(id, advertiser_id, meta_rank, last_seen, …)`; `public.creatives(ad_id, body_text, …)`. `meta_rank ASC NULLS LAST` ≈ impressions-desc.
- `frontend.app_ratings(user_email, advertiser_id, rating ∈ {like,neutral,dislike}, …)` — 36 like / 156 neutral / 263 dislike.
- `discovery.dynamic_keywords(id, keyword, source, status, sweeps_used, new_candidates_total, created_at, last_swept_at, kind)`; CHECK `status IN ('trial','active','retired')`; sweep selects `status IN ('trial','active')`.

---

### Task 1: Migrations — app_embeddings + dynamic_keywords taste columns

**Files:**
- Create: `db/schema/135_app_embeddings.sql`
- Create: `db/schema/136_dynamic_keywords_taste.sql`
- Test: `tests/db/test_content_pref_migrations.py`

**Interfaces:**
- Produces: table `frontend.app_embeddings(advertiser_id BIGINT PK, embedding REAL[], content_hash TEXT, dim INT, model TEXT, embedded_at TIMESTAMPTZ)`; `discovery.dynamic_keywords` gains `rationale TEXT`, `inspired_by BIGINT[]`, and `status` accepts `'pending'`.

- [ ] **Step 1: Write the failing test**

```python
# tests/db/test_content_pref_migrations.py
import os
import pytest
from sqlalchemy import text
from agf.db.engine import get_engine
from agf.db.migrate import apply_migrations

pytestmark = pytest.mark.skipif(
    not os.environ.get("AGF_TEST_DB_URL"), reason="needs AGF_TEST_DB_URL")


def test_migrations_apply_and_shape():
    eng = get_engine(os.environ["AGF_TEST_DB_URL"])
    apply_migrations(eng)
    with eng.begin() as c:
        cols = {r[0] for r in c.execute(text(
            "SELECT attname FROM pg_attribute "
            "WHERE attrelid='frontend.app_embeddings'::regclass "
            "AND attnum>0 AND NOT attisdropped"))}
        assert {"advertiser_id", "embedding", "content_hash", "dim",
                "model", "embedded_at"} <= cols
        dk = {r[0] for r in c.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='discovery' AND table_name='dynamic_keywords'"))}
        assert {"rationale", "inspired_by"} <= dk
        # 'pending' must now be accepted by the status check
        c.execute(text(
            "INSERT INTO discovery.dynamic_keywords (keyword, source, status, kind) "
            "VALUES ('zzz_test_pending', 'liked-mining', 'pending', 'app')"))
        c.execute(text(
            "DELETE FROM discovery.dynamic_keywords WHERE keyword='zzz_test_pending'"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it PYTHONPATH=src .venv/bin/python -m pytest tests/db/test_content_pref_migrations.py -v`
Expected: FAIL (relation `frontend.app_embeddings` does not exist).

- [ ] **Step 3: Write the migrations**

```sql
-- db/schema/135_app_embeddings.sql
-- Per-app content embedding (Gemini gemini-embedding-001 @768d, L2-normalized),
-- written by scripts/embed_apps.py. REAL[] (not pgvector) — numpy at read time,
-- ~1k apps. content_hash gates re-embedding to changed content only.
CREATE TABLE IF NOT EXISTS frontend.app_embeddings (
    advertiser_id BIGINT PRIMARY KEY,
    embedding     REAL[]      NOT NULL,
    content_hash  TEXT        NOT NULL,
    dim           INT         NOT NULL,
    model         TEXT        NOT NULL DEFAULT '',
    embedded_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

```sql
-- db/schema/136_dynamic_keywords_taste.sql
-- Taste-driven mined keywords (source='liked-mining') land as status='pending'
-- (sweep ignores it) until a human promotes them to 'trial'. rationale + the
-- inspired-by advertiser ids are shown in the review gate.
ALTER TABLE discovery.dynamic_keywords ADD COLUMN IF NOT EXISTS rationale TEXT;
ALTER TABLE discovery.dynamic_keywords ADD COLUMN IF NOT EXISTS inspired_by BIGINT[];
ALTER TABLE discovery.dynamic_keywords DROP CONSTRAINT IF EXISTS dynamic_keywords_status_check;
ALTER TABLE discovery.dynamic_keywords ADD CONSTRAINT dynamic_keywords_status_check
    CHECK (status = ANY (ARRAY['pending','trial','active','retired']));
```

- [ ] **Step 4: Run test to verify it passes**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it PYTHONPATH=src .venv/bin/python -m pytest tests/db/test_content_pref_migrations.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add db/schema/135_app_embeddings.sql db/schema/136_dynamic_keywords_taste.sql tests/db/test_content_pref_migrations.py
git commit -m "feat(db): app_embeddings table + dynamic_keywords taste columns (migrations 135/136)"
```

---

### Task 2: Embedding text builder (pure)

**Files:**
- Create: `src/agf/scoring/embeddings.py`
- Test: `tests/scoring/test_embeddings_pure.py`

**Interfaces:**
- Produces: `build_embed_text(*, app_name, tagline, genre, description, ad_copy: list[str], max_desc=1200, max_ads=8) -> str`; `content_hash(text: str) -> str`; `l2_normalize(vec: list[float]) -> list[float]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/scoring/test_embeddings_pure.py
import math
from agf.scoring.embeddings import build_embed_text, content_hash, l2_normalize


def test_build_embed_text_orders_and_dedups():
    t = build_embed_text(
        app_name="VocalSplit", tagline="Isolate vocals",
        genre="Music", description="Remove vocals from any song. " * 100,
        ad_copy=["Try it free", "Try it free", "AI stem separation"])
    assert t.startswith("VocalSplit")
    assert "Isolate vocals" in t and "Music" in t
    assert t.count("Try it free") == 1          # deduped
    assert "AI stem separation" in t
    assert len(t) < 4000                         # description truncated

def test_build_embed_text_handles_missing():
    t = build_embed_text(app_name="X", tagline=None, genre=None,
                         description=None, ad_copy=[])
    assert t.strip() == "X"

def test_content_hash_stable_and_changes():
    assert content_hash("a") == content_hash("a")
    assert content_hash("a") != content_hash("b")

def test_l2_normalize_unit():
    v = l2_normalize([3.0, 4.0])
    assert math.isclose(math.hypot(*v), 1.0, rel_tol=1e-6)
    assert l2_normalize([0.0, 0.0]) == [0.0, 0.0]   # zero stays zero, no div0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/scoring/test_embeddings_pure.py -v`
Expected: FAIL (`No module named agf.scoring.embeddings`).

- [ ] **Step 3: Write the minimal implementation**

```python
# src/agf/scoring/embeddings.py
"""Content embeddings for the per-user preference ranker.

Pure helpers (build_embed_text / content_hash / l2_normalize) + the Gemini
network call (embed_texts). scripts/embed_apps.py does the DB I/O.
"""
from __future__ import annotations

import hashlib
import math


def build_embed_text(*, app_name: str, tagline: str | None, genre: str | None,
                     description: str | None, ad_copy: list[str],
                     max_desc: int = 1200, max_ads: int = 8) -> str:
    """One string per app: name, tagline, category, truncated description, then
    the first max_ads DISTINCT ad-copy lines (order preserved)."""
    parts: list[str] = [app_name.strip()]
    if tagline:
        parts.append(tagline.strip())
    if genre:
        parts.append(genre.strip())
    if description:
        parts.append(description.strip()[:max_desc])
    seen: set[str] = set()
    kept = 0
    for line in ad_copy:
        line = (line or "").strip()
        if not line or line in seen:
            continue
        seen.add(line)
        parts.append(line)
        kept += 1
        if kept >= max_ads:
            break
    return "\n".join(p for p in parts if p)


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm < 1e-12:
        return list(vec)
    return [x / norm for x in vec]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/scoring/test_embeddings_pure.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agf/scoring/embeddings.py tests/scoring/test_embeddings_pure.py
git commit -m "feat(scoring): pure embed-text builder + hash + L2-normalize"
```

---

### Task 3: Gemini embedding call

**Files:**
- Modify: `src/agf/scoring/embeddings.py` (add `embed_texts`)
- Test: `tests/scoring/test_embeddings_gemini.py`

**Interfaces:**
- Consumes: `l2_normalize` (Task 2).
- Produces: `embed_texts(texts: list[str], *, api_key: str | None = None, model="gemini-embedding-001", dim=768, _post=None) -> list[list[float]]` — returns one L2-normalized `dim`-length vector per input; raises `EmbedError` on HTTP failure or dimension mismatch. `_post` is an injectable transport for tests `(url, json_body) -> dict`.

- [ ] **Step 1: Write the failing test**

```python
# tests/scoring/test_embeddings_gemini.py
import math
import pytest
from agf.scoring.embeddings import embed_texts, EmbedError


def _fake_post_factory(values):
    def _post(url, body):
        assert body["outputDimensionality"] == 768
        return {"embedding": {"values": values}}
    return _post


def test_embed_texts_normalizes():
    out = embed_texts(["hi"], api_key="k", _post=_fake_post_factory([3.0, 4.0] + [0.0] * 766))
    assert len(out) == 1
    assert math.isclose(math.sqrt(sum(x * x for x in out[0])), 1.0, rel_tol=1e-6)


def test_embed_texts_dim_mismatch_raises():
    with pytest.raises(EmbedError):
        embed_texts(["hi"], api_key="k", _post=_fake_post_factory([1.0, 2.0]))  # only 2 dims
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/scoring/test_embeddings_gemini.py -v`
Expected: FAIL (`cannot import name 'embed_texts'`).

- [ ] **Step 3: Add the implementation**

```python
# append to src/agf/scoring/embeddings.py
import json
import os
import urllib.request

_EMBED_URL = ("https://generativelanguage.googleapis.com/v1beta/models/"
              "{model}:embedContent?key={key}")


class EmbedError(RuntimeError):
    pass


def _http_post(url: str, body: dict) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except Exception as exc:  # noqa: BLE001 - surface any transport failure uniformly
        raise EmbedError(f"gemini embed POST failed: {exc!r}") from exc


def embed_texts(texts: list[str], *, api_key: str | None = None,
                model: str = "gemini-embedding-001", dim: int = 768,
                _post=None) -> list[list[float]]:
    """L2-normalized dim-length embedding per text. One request per text
    (embedContent is single-input); callers batch over apps and may sleep
    between calls. _post is injectable for tests."""
    key = api_key or os.environ.get("GEMINI_API_KEY")
    if not key:
        raise EmbedError("no GEMINI_API_KEY")
    post = _post or _http_post
    url = _EMBED_URL.format(model=model, key=key)
    out: list[list[float]] = []
    for text in texts:
        resp = post(url, {"model": f"models/{model}",
                          "content": {"parts": [{"text": text}]},
                          "outputDimensionality": dim})
        values = resp.get("embedding", {}).get("values")
        if not values or len(values) != dim:
            raise EmbedError(f"expected {dim} dims, got {0 if not values else len(values)}")
        out.append(l2_normalize([float(x) for x in values]))
    return out
```

- [ ] **Step 4: Run unit test (mocked) + a live probe**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/scoring/test_embeddings_gemini.py -v`
Expected: PASS.

Live probe (real Gemini, confirms key + shape end-to-end):
```bash
PYTHONPATH=src .venv/bin/python -c "from agf.scoring.embeddings import embed_texts; v=embed_texts(['AI vocal remover app'])[0]; print('dims', len(v), 'norm', round(sum(x*x for x in v)**0.5,4))"
```
Expected: `dims 768 norm 1.0`.

- [ ] **Step 5: Commit**

```bash
git add src/agf/scoring/embeddings.py tests/scoring/test_embeddings_gemini.py
git commit -m "feat(scoring): Gemini embed_texts (768d, normalized, injectable transport)"
```

---

### Task 4: Incremental embed script

**Files:**
- Create: `scripts/embed_apps.py`
- Test: `tests/scoring/test_embed_apps.py`

**Interfaces:**
- Consumes: `build_embed_text`, `content_hash` (Task 2), `embed_texts` (Task 3); `agf.db.engine.begin`.
- Produces: `embed_apps(conn, *, embed_fn=embed_texts, limit=None) -> dict` (counts `{"embedded": n, "skipped": m}`); upserts `frontend.app_embeddings`. `embed_fn` injectable for tests.

- [ ] **Step 1: Write the failing test**

```python
# tests/scoring/test_embed_apps.py
import os
import pytest
from sqlalchemy import text
from agf.db.engine import get_engine
from agf.db.migrate import apply_migrations
from scripts.embed_apps import embed_apps

pytestmark = pytest.mark.skipif(
    not os.environ.get("AGF_TEST_DB_URL"), reason="needs AGF_TEST_DB_URL")


def test_embed_apps_is_incremental(monkeypatch):
    eng = get_engine(os.environ["AGF_TEST_DB_URL"])
    apply_migrations(eng)
    calls = {"n": 0}
    def fake_embed(texts, **kw):
        calls["n"] += len(texts)
        return [[1.0] + [0.0] * 767 for _ in texts]
    with eng.begin() as conn:
        # Use a real advertiser already present in the matview (test DB is seeded by Go IT).
        row = conn.execute(text(
            "SELECT advertiser_id FROM frontend.emerging_scaling_apps LIMIT 1")).first()
        if row is None:
            pytest.skip("no matview rows in test DB")
        first = embed_apps(conn, embed_fn=fake_embed)
        assert first["embedded"] >= 1
        n_after_first = calls["n"]
        second = embed_apps(conn, embed_fn=fake_embed)   # nothing changed
        assert second["embedded"] == 0
        assert calls["n"] == n_after_first               # no re-embed
```

> **Note for implementer:** `frontend.emerging_scaling_apps` is a **materialized view** (read-only). The IT test relies on rows the Go integration seed already creates; if empty it skips. Do not try to INSERT into the matview.

- [ ] **Step 2: Run test to verify it fails**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it PYTHONPATH=src .venv/bin/python -m pytest tests/scoring/test_embed_apps.py -v`
Expected: FAIL (`No module named scripts.embed_apps`).

- [ ] **Step 3: Write the script**

```python
# scripts/embed_apps.py
"""Embed each scored app's content into frontend.app_embeddings (incremental:
only new or content-changed apps). Mirrors the GemScore pattern — Python writes.

Run:
    PYTHONPATH=src AGF_DATABASE_URL=postgresql://agf:agf@localhost:5433/agf \\
    .venv/bin/python scripts/embed_apps.py
"""
import os
import sys

from sqlalchemy import text

from agf.db.engine import begin, get_engine
from agf.scoring.embeddings import build_embed_text, content_hash, embed_texts

_APPS = text(
    "SELECT m.advertiser_id, m.app_name, m.tagline, m.niche_keyword, "
    "       a.genre, a.description "
    "FROM frontend.emerging_scaling_apps m "
    "LEFT JOIN public.apps a ON a.id = m.app_id")

_AD_COPY = text(
    "SELECT ad.advertiser_id, c.body_text "
    "FROM public.ads ad JOIN public.creatives c ON c.ad_id = ad.id "
    "WHERE ad.advertiser_id = ANY(:ids) AND c.body_text IS NOT NULL "
    "  AND c.body_text <> '' "
    "ORDER BY ad.advertiser_id, ad.meta_rank ASC NULLS LAST, ad.last_seen DESC")

_EXISTING = text("SELECT advertiser_id, content_hash FROM frontend.app_embeddings")

_UPSERT = text(
    "INSERT INTO frontend.app_embeddings "
    "(advertiser_id, embedding, content_hash, dim, model, embedded_at) "
    "VALUES (:a, :e, :h, :d, :m, now()) "
    "ON CONFLICT (advertiser_id) DO UPDATE SET "
    "  embedding=EXCLUDED.embedding, content_hash=EXCLUDED.content_hash, "
    "  dim=EXCLUDED.dim, model=EXCLUDED.model, embedded_at=now()")


def _ad_copy_by_advertiser(conn, ids):
    out: dict[int, list[str]] = {}
    if not ids:
        return out
    for aid, body in conn.execute(_AD_COPY, {"ids": list(ids)}):
        out.setdefault(aid, []).append(body)
    return out


def embed_apps(conn, *, embed_fn=embed_texts, limit=None) -> dict:
    rows = list(conn.execute(_APPS))
    ids = [r.advertiser_id for r in rows]
    ad_copy = _ad_copy_by_advertiser(conn, ids)
    existing = {a: h for a, h in conn.execute(_EXISTING)}

    pending = []  # (advertiser_id, text, hash)
    for r in rows:
        text_ = build_embed_text(
            app_name=r.app_name or "", tagline=r.tagline,
            genre=r.genre, description=r.description,
            ad_copy=ad_copy.get(r.advertiser_id, []))
        if not text_.strip():
            continue
        h = content_hash(text_)
        if existing.get(r.advertiser_id) == h:
            continue
        pending.append((r.advertiser_id, text_, h))
        if limit and len(pending) >= limit:
            break

    embedded = 0
    for aid, text_, h in pending:
        vec = embed_fn([text_])[0]
        conn.execute(_UPSERT, {"a": aid, "e": vec, "h": h, "d": len(vec),
                               "m": "gemini-embedding-001"})
        embedded += 1
    return {"embedded": embedded, "skipped": len(rows) - embedded}


def main() -> int:
    eng = get_engine()
    with begin(eng) as conn:
        stats = embed_apps(conn)
    print(f"[embed] {stats}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it PYTHONPATH=src .venv/bin/python -m pytest tests/scoring/test_embed_apps.py -v`
Expected: PASS (or SKIP if the test DB matview is empty — then run the live probe in Task 10).

- [ ] **Step 5: Commit**

```bash
git add scripts/embed_apps.py tests/scoring/test_embed_apps.py
git commit -m "feat(scoring): incremental embed_apps script -> frontend.app_embeddings"
```

---

### Task 5: Content preference model (pure)

**Files:**
- Create: `src/agf/scoring/content_preference.py`
- Test: `tests/scoring/test_content_preference.py`

**Interfaces:**
- Produces: `ContentPreferenceModel` with `@classmethod fit(liked, disliked, min_likes=8)` where `liked`/`disliked` are `list[tuple[int, str, list[float]]]` = `(advertiser_id, app_name, unit_vec)`; instance methods `score(vec) -> float` (cosine vs direction), `p_like(raw) -> float`, `why(vec) -> str`, attribute `version` (`'content-sim-v1'` or `'coldstart'`). Module consts `MIN_LIKES = 8`.

- [ ] **Step 1: Write the failing test**

```python
# tests/scoring/test_content_preference.py
import numpy as np
from agf.scoring.content_preference import ContentPreferenceModel, MIN_LIKES


def _unit(v):
    v = np.asarray(v, float); return list(v / np.linalg.norm(v))


def _make(n, base, label_name):
    # n vectors clustered near `base`
    out = []
    for i in range(n):
        v = np.array(base, float) + 0.01 * np.array([i % 3, (i + 1) % 3, (i + 2) % 3])
        out.append((i, f"{label_name}{i}", _unit(v)))
    return out


def test_coldstart_below_min_likes():
    m = ContentPreferenceModel.fit(liked=_make(2, [1, 0, 0], "L"),
                                   disliked=_make(50, [0, 1, 0], "D"))
    assert m.version == "coldstart"


def test_direction_scores_likes_above_dislikes():
    liked = _make(MIN_LIKES, [1, 0, 0], "L")
    disliked = _make(40, [0, 1, 0], "D")
    m = ContentPreferenceModel.fit(liked=liked, disliked=disliked)
    assert m.version == "content-sim-v1"
    like_score = np.mean([m.score(v) for _, _, v in liked])
    dis_score = np.mean([m.score(v) for _, _, v in disliked])
    assert like_score > dis_score
    # p_like monotonic + bounded
    assert 0.0 <= m.p_like(dis_score) < m.p_like(like_score) <= 1.0


def test_why_names_nearest_liked():
    liked = _make(MIN_LIKES, [1, 0, 0], "Like")
    disliked = _make(40, [0, 1, 0], "D")
    m = ContentPreferenceModel.fit(liked=liked, disliked=disliked)
    assert "Like" in m.why(_unit([1, 0, 0]))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/scoring/test_content_preference.py -v`
Expected: FAIL (`No module named agf.scoring.content_preference`).

- [ ] **Step 3: Write the model**

```python
# src/agf/scoring/content_preference.py
"""Per-user similarity-to-likes ranker over content embeddings.

direction = normalize(mean(liked) - mean(disliked)); score(app) = cosine(app, direction)
(vectors are unit-norm, so cosine == dot). p_like is a Platt-style 1-D calibration
of the raw score against the labels. why names the nearest liked app. Pure — no DB.
"""
from __future__ import annotations

import numpy as np

MIN_LIKES = 8


def _l2(v):
    n = np.linalg.norm(v)
    return v if n < 1e-12 else v / n


def _fit_platt(raws, labels, iters=500, lr=0.5):
    """Tiny gradient-descent logistic of label ~ a*raw + b. Returns (a, b).
    Bounded iterations, no external optimizer (avoids statsmodels fragility)."""
    x = np.asarray(raws, float)
    y = np.asarray(labels, float)
    a, b = 1.0, 0.0
    n = max(len(x), 1)
    for _ in range(iters):
        p = 1.0 / (1.0 + np.exp(-(a * x + b)))
        ga = np.dot(p - y, x) / n
        gb = np.sum(p - y) / n
        a -= lr * ga
        b -= lr * gb
    if not np.isfinite(a) or not np.isfinite(b):
        return 1.0, 0.0
    return float(a), float(b)


class ContentPreferenceModel:
    def __init__(self, version, direction=None, calib=(1.0, 0.0),
                 liked_vecs=None, liked_names=None):
        self.version = version
        self._dir = direction
        self._a, self._b = calib
        self._liked_vecs = liked_vecs        # np.ndarray (k, dim) or None
        self._liked_names = liked_names or []

    @classmethod
    def fit(cls, liked, disliked, min_likes=MIN_LIKES):
        if len(liked) < min_likes:
            return cls("coldstart")
        L = np.array([v for _, _, v in liked], float)
        like_c = L.mean(axis=0)
        if disliked:
            dis_c = np.array([v for _, _, v in disliked], float).mean(axis=0)
        else:
            dis_c = np.zeros_like(like_c)
        direction = _l2(like_c - dis_c)
        labeled = [(v, 1) for _, _, v in liked] + [(v, 0) for _, _, v in disliked]
        raws = [float(np.dot(v, direction)) for v, _ in labeled]
        labels = [y for _, y in labeled]
        calib = _fit_platt(raws, labels)
        names = [n for _, n, _ in liked]
        return cls("content-sim-v1", direction, calib, L, names)

    def score(self, vec) -> float:
        if self.version == "coldstart":
            return 0.0
        return float(np.dot(np.asarray(vec, float), self._dir))

    def p_like(self, raw: float) -> float:
        return float(1.0 / (1.0 + np.exp(-(self._a * raw + self._b))))

    def why(self, vec) -> str:
        if self.version == "coldstart" or self._liked_vecs is None:
            return ""
        sims = self._liked_vecs @ np.asarray(vec, float)
        i = int(np.argmax(sims))
        return f"similar to {self._liked_names[i]}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/scoring/test_content_preference.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agf/scoring/content_preference.py tests/scoring/test_content_preference.py
git commit -m "feat(scoring): similarity-to-likes content preference model (pure)"
```

---

### Task 6: Content preference scorer + worker repoint + embed stage

**Files:**
- Create: `scripts/score_content_preferences.py`
- Modify: `src/agf/workers/main.py` (imports line ~92; preference stage ~610; add embed stage before it)
- Test: `tests/scoring/test_score_content_preferences.py`

**Interfaces:**
- Consumes: `ContentPreferenceModel` (Task 5); `frontend.app_embeddings` (Task 4); `frontend.app_ratings`; matview `gem_score` for cold-start.
- Produces: `score_all_users(conn) -> dict` (per-user counts) writing `frontend.preference_scores` with `model_version='content-sim-v1'` (or `'coldstart'`). Worker calls it where it currently calls `scripts.score_preferences.score_all_users`, plus a new embed stage gated by `AGF_EMBED_APPS` (default on).

- [ ] **Step 1: Write the failing test**

```python
# tests/scoring/test_score_content_preferences.py
import os
import pytest
from sqlalchemy import text
from agf.db.engine import get_engine
from agf.db.migrate import apply_migrations
from scripts.score_content_preferences import score_all_users

pytestmark = pytest.mark.skipif(
    not os.environ.get("AGF_TEST_DB_URL"), reason="needs AGF_TEST_DB_URL")

EMAIL = "ct_pref@test.local"


def test_scores_written_for_user():
    eng = get_engine(os.environ["AGF_TEST_DB_URL"])
    apply_migrations(eng)
    # Synthetic advertiser_ids — independent of matview row count. The scorer reads
    # embeddings + ratings BY advertiser_id; the matview only supplies names /
    # cold-start gem_score, so labeled apps need not be matview members. This keeps
    # the test deterministic regardless of how many rows the Go IT seed created.
    adv = list(range(900001, 900015))            # 14 ids: 10 like + 4 dislike
    e1 = [1.0] + [0.0] * 767                      # liked cluster
    e2 = [0.0, 1.0] + [0.0] * 766                 # disliked cluster
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM frontend.app_ratings WHERE user_email=:e"), {"e": EMAIL})
        conn.execute(text("DELETE FROM frontend.preference_scores WHERE user_email=:e"), {"e": EMAIL})
        conn.execute(text("DELETE FROM frontend.app_embeddings WHERE advertiser_id = ANY(:ids)"), {"ids": adv})
        for i, a in enumerate(adv):
            vec = e1 if i < 10 else e2
            conn.execute(text(
                "INSERT INTO frontend.app_embeddings (advertiser_id,embedding,content_hash,dim,model) "
                "VALUES (:a,:v,:h,768,'t') ON CONFLICT (advertiser_id) DO UPDATE SET embedding=EXCLUDED.embedding"),
                {"a": a, "v": vec, "h": f"h{i}"})
            rating = "like" if i < 10 else "dislike"   # 10 likes -> above MIN_LIKES (8)
            conn.execute(text(
                "INSERT INTO frontend.app_ratings (user_email,advertiser_id,rating,features) "
                "VALUES (:e,:a,:r,'{}'::jsonb)"), {"e": EMAIL, "a": a, "r": rating})
        counts = score_all_users(conn)
        assert counts.get(EMAIL, 0) >= 12          # scorer ranks every embedding
        versions = [r[0] for r in conn.execute(text(
            "SELECT DISTINCT model_version FROM frontend.preference_scores "
            "WHERE user_email=:e"), {"e": EMAIL})]
        assert versions == ["content-sim-v1"]      # 10 likes -> active model, not coldstart
        conn.execute(text("DELETE FROM frontend.app_ratings WHERE user_email=:e"), {"e": EMAIL})
        conn.execute(text("DELETE FROM frontend.preference_scores WHERE user_email=:e"), {"e": EMAIL})
        conn.execute(text("DELETE FROM frontend.app_embeddings WHERE advertiser_id = ANY(:ids)"), {"ids": adv})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it PYTHONPATH=src .venv/bin/python -m pytest tests/scoring/test_score_content_preferences.py -v`
Expected: FAIL (`No module named scripts.score_content_preferences`).

- [ ] **Step 3: Write the scorer**

```python
# scripts/score_content_preferences.py
"""Per-user similarity-to-likes scoring over content embeddings → preference_scores.

Supersedes scripts/score_preferences.py in the serving path (the 6-axis Logit now
lives in scripts/analyze_axis_coefficients.py as the GemScore-tuning knob).

Run:
    PYTHONPATH=src AGF_DATABASE_URL=postgresql://agf:agf@localhost:5433/agf \\
    .venv/bin/python scripts/score_content_preferences.py
"""
import os
import sys

from sqlalchemy import text

from agf.db.engine import begin, get_engine
from agf.scoring.content_preference import ContentPreferenceModel

_EMB = text("SELECT advertiser_id, embedding FROM frontend.app_embeddings")
_NAMES = text("SELECT advertiser_id, app_name, gem_score "
              "FROM frontend.emerging_scaling_apps")
_LABELS = text("SELECT advertiser_id, rating FROM frontend.app_ratings "
               "WHERE user_email = :e")
_EMAILS = text("SELECT DISTINCT user_email FROM frontend.app_ratings")


def _load(conn):
    emb = {a: [float(x) for x in v] for a, v in conn.execute(_EMB)}
    meta = {r.advertiser_id: (r.app_name, r.gem_score)
            for r in conn.execute(_NAMES)}
    return emb, meta


def score_user(conn, user_email, emb, meta) -> int:
    labels = {a: r for a, r in conn.execute(_LABELS, {"e": user_email})}
    liked = [(a, meta.get(a, ("", 0))[0] or "", emb[a])
             for a, r in labels.items() if r == "like" and a in emb]
    disliked = [(a, meta.get(a, ("", 0))[0] or "", emb[a])
                for a, r in labels.items() if r == "dislike" and a in emb]
    model = ContentPreferenceModel.fit(liked, disliked)

    scored = []
    for a, vec in emb.items():
        if model.version == "coldstart":
            gs = meta.get(a, ("", 0))[1] or 0.0
            scored.append((a, float(gs) / 100.0, "", float(gs)))
        else:
            raw = model.score(vec)
            scored.append((a, model.p_like(raw), model.why(vec), raw))
    scored.sort(key=lambda t: t[3], reverse=True)   # rank by raw score / gem_score

    conn.execute(text("DELETE FROM frontend.preference_scores WHERE user_email=:e"),
                 {"e": user_email})
    if scored:
        conn.execute(text(
            "INSERT INTO frontend.preference_scores "
            "(user_email, advertiser_id, p_like, uncertainty, queue_rank, why, "
            " model_version, scored_at) VALUES "
            "(:e,:a,:p,:u,:q,:w,:v, now())"),
            [{"e": user_email, "a": a, "p": p,
              "u": 1.0 - abs(2.0 * p - 1.0), "q": q, "w": why, "v": model.version}
             for q, (a, p, why, _raw) in enumerate(scored)])
    print(f"[cpref] {user_email}: model={model.version} scored={len(scored)} "
          f"likes={len(liked)}", flush=True)
    return len(scored)


def score_all_users(conn) -> dict:
    emb, meta = _load(conn)
    out = {}
    for (email,) in conn.execute(_EMAILS):
        out[email] = score_user(conn, email, emb, meta)
    return out


def main() -> int:
    with begin(get_engine()) as conn:
        print(f"[cpref] {score_all_users(conn)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it PYTHONPATH=src .venv/bin/python -m pytest tests/scoring/test_score_content_preferences.py -v`
Expected: PASS.

- [ ] **Step 5: Repoint the worker + add the embed stage**

In `src/agf/workers/main.py`, change the import (line ~92):

```python
from scripts.embed_apps import embed_apps                       # content embeddings
from scripts.score_content_preferences import score_all_users   # content preference scoring
```

Replace the preference stage block (the `if score_after and os.environ.get("AGF_SCORE_PREFERENCES", "1") != "0":` block, ~line 610) with an embed stage followed by the unchanged-shape scoring call:

```python
    # 2a) content embeddings — embed new/changed apps so the preference scorer
    #     ranks on current content. Best-effort: a failure must not stall the cycle.
    if score_after and os.environ.get("AGF_EMBED_APPS", "1") != "0":
        try:
            with begin(engine) as conn:
                emb_stats = embed_apps(conn)
            print(f"[worker] embeddings: {emb_stats}", flush=True)
        except Exception as exc:
            print(f"[worker] embedding stage failed: {exc!r}",
                  file=sys.stderr, flush=True)

    # 2b) preference re-scoring — per-user similarity-to-likes over embeddings.
    if score_after and os.environ.get("AGF_SCORE_PREFERENCES", "1") != "0":
        try:
            with begin(engine) as conn:
                pref_counts = score_all_users(conn)
            print(f"[worker] preference scoring: {pref_counts}", flush=True)
        except Exception as exc:
            print(f"[worker] preference scoring failed: {exc!r}",
                  file=sys.stderr, flush=True)
```

Verify it imports/compiles:
Run: `PYTHONPATH=src .venv/bin/python -c "import agf.workers.main"`
Expected: no output, exit 0.

- [ ] **Step 6: Commit**

```bash
git add scripts/score_content_preferences.py tests/scoring/test_score_content_preferences.py src/agf/workers/main.py
git commit -m "feat(scoring): content preference scorer + worker embed/score stages"
```

---

### Task 7: Move the 6-axis Logit off the serving path

**Files:**
- Create: `scripts/analyze_axis_coefficients.py`
- Modify: `tests/scoring/test_score_preferences_e2e.py` (point at the analysis entry / drop the serving-write assertion)
- Keep: `src/agf/scoring/preference.py` (unchanged library)

**Interfaces:**
- Consumes: `agf.scoring.preference.PreferenceModel` (existing).
- Produces: `analyze(conn, user_email) -> dict` returning `{"version", "coefficients"}`; prints the coefficients as the GemScore-reweight proposal. **Writes nothing to `preference_scores`.**

- [ ] **Step 1: Write the failing test**

```python
# tests/scoring/test_analyze_axis_coefficients.py
import os
import pytest
from sqlalchemy import text
from agf.db.engine import get_engine
from scripts.analyze_axis_coefficients import analyze

pytestmark = pytest.mark.skipif(
    not os.environ.get("AGF_TEST_DB_URL"), reason="needs AGF_TEST_DB_URL")


def test_analyze_returns_coefficients_without_writing(monkeypatch):
    eng = get_engine(os.environ["AGF_TEST_DB_URL"])
    with eng.begin() as conn:
        before = conn.execute(text("SELECT count(*) FROM frontend.preference_scores")).scalar()
        out = analyze(conn, "nobody@test.local")     # no labels -> heuristic, still returns
        assert "coefficients" in out and "version" in out
        after = conn.execute(text("SELECT count(*) FROM frontend.preference_scores")).scalar()
        assert before == after                        # analysis must not write serving rows
```

- [ ] **Step 2: Run test to verify it fails**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it PYTHONPATH=src .venv/bin/python -m pytest tests/scoring/test_analyze_axis_coefficients.py -v`
Expected: FAIL (`No module named scripts.analyze_axis_coefficients`).

- [ ] **Step 3: Write the analysis script**

```python
# scripts/analyze_axis_coefficients.py
"""The 6-axis statsmodels Logit, OFF the serving path. Its fitted coefficients are
the GemScore-reweight proposal (interpretable knob: which axes drive the user's
likes). Serving ranking now comes from scripts/score_content_preferences.py.

Run:
    PYTHONPATH=src AGF_DATABASE_URL=postgresql://agf:agf@localhost:5433/agf \\
    .venv/bin/python scripts/analyze_axis_coefficients.py [user_email]
"""
import json
import sys

from sqlalchemy import text

from agf.db.engine import begin, get_engine
from agf.scoring.preference import PreferenceModel

_LABELS = text("SELECT rating, features FROM frontend.app_ratings WHERE user_email=:e")


def _rows(conn, user_email):
    out = []
    for rating, features in conn.execute(_LABELS, {"e": user_email}):
        f = features if isinstance(features, dict) else (
            json.loads(features) if features else {})
        out.append((rating, f))
    return out


def analyze(conn, user_email: str) -> dict:
    model = PreferenceModel.fit(_rows(conn, user_email))
    print(f"[axis] {user_email}: model={model.version} "
          f"coefs={json.dumps(model.coefficients)}", flush=True)
    return {"version": model.version, "coefficients": model.coefficients}


def main() -> int:
    email = sys.argv[1] if len(sys.argv) > 1 else "founder@fielddesk.local"
    with begin(get_engine()) as conn:
        analyze(conn, email)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Retire the old serving script's e2e test**

`scripts/score_preferences.py` is no longer wired into the worker. Update `tests/scoring/test_score_preferences_e2e.py`: if it asserts the worker/serving behavior, replace its import with `scripts.analyze_axis_coefficients` and assert coefficients are returned without a `preference_scores` write (mirror Step 1). Leave `scripts/score_preferences.py` on disk (harmless, unimported) OR delete it and remove the test file — implementer's choice; if deleted, `git rm` both and skip this step's edit.

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it PYTHONPATH=src .venv/bin/python -m pytest tests/scoring/test_analyze_axis_coefficients.py tests/scoring/test_score_preferences_e2e.py -v`
Expected: PASS (or the e2e file removed).

- [ ] **Step 5: Commit**

```bash
git add scripts/analyze_axis_coefficients.py tests/scoring/test_analyze_axis_coefficients.py tests/scoring/test_score_preferences_e2e.py
git commit -m "refactor(scoring): 6-axis Logit becomes off-serving GemScore-tuning analysis"
```

---

### Task 8: Taste-driven keyword mining (pure dedup + cheap-Gemini proposer)

**Files:**
- Create: `src/agf/discovery/liked_keyword_mining.py`
- Test: `tests/discovery/test_liked_keyword_mining.py`

**Interfaces:**
- Produces: dataclass `Candidate(phrase: str, rationale: str, inspired_by: list[int])`; `dedup_candidates(candidates, existing: set[str]) -> list[Candidate]` (case-insensitive, drops phrases already known); `propose_phrases(liked_apps: list[dict], *, api_key=None, model="gemini-2.0-flash-lite", _post=None) -> list[Candidate]` where each `liked_app` is `{"advertiser_id", "name", "description", "ad_copy"}`. `_post(url, body) -> dict` injectable.

- [ ] **Step 1: Write the failing test**

```python
# tests/discovery/test_liked_keyword_mining.py
from agf.discovery.liked_keyword_mining import (
    Candidate, dedup_candidates, propose_phrases)


def test_dedup_is_case_insensitive():
    cands = [Candidate("AI Vocal Remover", "r", [1]),
             Candidate("stem splitter", "r", [2])]
    out = dedup_candidates(cands, existing={"ai vocal remover"})
    assert [c.phrase for c in out] == ["stem splitter"]


def test_propose_parses_gemini_json():
    def _post(url, body):
        # emulate a ResponseSchema JSON reply in the candidates text part
        return {"candidates": [{"content": {"parts": [{"text":
            '{"phrases":[{"phrase":"ai stem separation","rationale":"like VocalSplit",'
            '"inspired_by":[7953]}]}'}]}}]}
    out = propose_phrases([{"advertiser_id": 7953, "name": "VocalSplit",
                            "description": "remove vocals", "ad_copy": ["AI stems"]}],
                          api_key="k", _post=_post)
    assert len(out) == 1
    assert out[0].phrase == "ai stem separation"
    assert out[0].inspired_by == [7953]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/discovery/test_liked_keyword_mining.py -v`
Expected: FAIL (`No module named agf.discovery.liked_keyword_mining`).

- [ ] **Step 3: Write the module**

```python
# src/agf/discovery/liked_keyword_mining.py
"""Taste-driven keyword miner: a cheap Gemini model proposes Meta-Ad-Library
search phrases that would find apps SIMILAR to the user's liked apps. Distinct
from keyword_mining.py (the TF-IDF flywheel over all scored gems). Proposed
phrases land as dynamic_keywords status='pending' until a human promotes them.
"""
from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass, field

_GEN_URL = ("https://generativelanguage.googleapis.com/v1beta/models/"
            "{model}:generateContent?key={key}")

_SCHEMA = {
    "type": "object",
    "properties": {"phrases": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "phrase": {"type": "string"},
            "rationale": {"type": "string"},
            "inspired_by": {"type": "array", "items": {"type": "integer"}},
        },
        "required": ["phrase", "rationale", "inspired_by"]}}},
    "required": ["phrases"],
}

_PROMPT = (
    "You pick Meta Ad Library search phrases that would surface NEW mobile apps "
    "similar to the ones a user liked. Propose up to {n} short phrases (2-4 words, "
    "lowercase, the kind of words these apps put in their ad copy or store listing). "
    "Avoid generic ad-speak (free trial, download now). For each phrase give a one-line "
    "rationale and the advertiser_id(s) it was inspired by.\n\nLiked apps:\n{apps}")


@dataclass
class Candidate:
    phrase: str
    rationale: str
    inspired_by: list[int] = field(default_factory=list)


class MiningError(RuntimeError):
    pass


def _http_post(url: str, body: dict) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def dedup_candidates(candidates: list[Candidate], existing: set[str]) -> list[Candidate]:
    existing_lc = {e.lower() for e in existing}
    out, seen = [], set()
    for c in candidates:
        key = c.phrase.strip().lower()
        if not key or key in existing_lc or key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def _format_apps(liked_apps: list[dict]) -> str:
    lines = []
    for a in liked_apps:
        copy = " / ".join((a.get("ad_copy") or [])[:3])
        lines.append(f"- [{a['advertiser_id']}] {a.get('name','')}: "
                     f"{(a.get('description') or '')[:200]} || {copy}")
    return "\n".join(lines)


def propose_phrases(liked_apps: list[dict], *, api_key: str | None = None,
                    model: str = "gemini-2.0-flash-lite", n: int = 15,
                    _post=None) -> list[Candidate]:
    if not liked_apps:
        return []
    key = api_key or os.environ.get("GEMINI_API_KEY")
    if not key:
        raise MiningError("no GEMINI_API_KEY")
    post = _post or _http_post
    body = {
        "contents": [{"parts": [{"text": _PROMPT.format(
            n=n, apps=_format_apps(liked_apps))}]}],
        "generationConfig": {"responseMimeType": "application/json",
                             "responseSchema": _SCHEMA},
    }
    resp = post(_GEN_URL.format(model=model, key=key), body)
    try:
        text = resp["candidates"][0]["content"]["parts"][0]["text"]
        parsed = json.loads(text)
    except (KeyError, IndexError, json.JSONDecodeError) as exc:
        raise MiningError(f"unparseable Gemini reply: {exc!r}") from exc
    out = []
    for p in parsed.get("phrases", []):
        phrase = (p.get("phrase") or "").strip()
        if phrase:
            out.append(Candidate(phrase, (p.get("rationale") or "").strip(),
                                 [int(x) for x in (p.get("inspired_by") or [])]))
    return out
```

- [ ] **Step 4: Run unit tests (mocked)**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/discovery/test_liked_keyword_mining.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agf/discovery/liked_keyword_mining.py tests/discovery/test_liked_keyword_mining.py
git commit -m "feat(discovery): taste-driven keyword miner (cheap Gemini, pure dedup)"
```

---

### Task 9: Mining + review scripts (the human gate)

**Files:**
- Create: `scripts/mine_liked_keywords.py`
- Create: `scripts/review_mined_keywords.py`
- Test: `tests/discovery/test_mine_review_keywords.py`

**Interfaces:**
- Consumes: `propose_phrases`, `dedup_candidates`, `Candidate` (Task 8).
- Produces: `mine(conn, user_email, *, propose_fn=propose_phrases) -> int` inserting `pending` rows; `list_pending(conn) -> list[dict]`; `promote(conn, keyword) -> int` flipping `pending → trial`.

- [ ] **Step 1: Write the failing test**

```python
# tests/discovery/test_mine_review_keywords.py
import os
import pytest
from sqlalchemy import text
from agf.db.engine import get_engine
from agf.db.migrate import apply_migrations
from agf.discovery.liked_keyword_mining import Candidate
from scripts.mine_liked_keywords import mine
from scripts.review_mined_keywords import list_pending, promote

pytestmark = pytest.mark.skipif(
    not os.environ.get("AGF_TEST_DB_URL"), reason="needs AGF_TEST_DB_URL")

EMAIL = "mine@test.local"


def test_mine_inserts_pending_then_promote_to_trial():
    eng = get_engine(os.environ["AGF_TEST_DB_URL"])
    apply_migrations(eng)
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM discovery.dynamic_keywords WHERE source='liked-mining'"))
        adv = conn.execute(text(
            "SELECT advertiser_id FROM frontend.emerging_scaling_apps LIMIT 1")).scalar()
        if adv is None:
            pytest.skip("no matview rows")
        conn.execute(text("DELETE FROM frontend.app_ratings WHERE user_email=:e"), {"e": EMAIL})
        conn.execute(text(
            "INSERT INTO frontend.app_ratings (user_email,advertiser_id,rating,features) "
            "VALUES (:e,:a,'like','{}'::jsonb)"), {"e": EMAIL, "a": adv})

        def fake_propose(apps, **kw):
            return [Candidate("zzz ai stem separation", "like X", [adv])]

        n = mine(conn, EMAIL, propose_fn=fake_propose)
        assert n == 1
        pend = [p for p in list_pending(conn) if p["keyword"] == "zzz ai stem separation"]
        assert pend and pend[0]["status"] == "pending"
        # sweep selector must ignore pending
        swept = conn.execute(text(
            "SELECT count(*) FROM discovery.dynamic_keywords "
            "WHERE keyword='zzz ai stem separation' AND status IN ('trial','active')")).scalar()
        assert swept == 0
        promote(conn, "zzz ai stem separation")
        now_trial = conn.execute(text(
            "SELECT status FROM discovery.dynamic_keywords WHERE keyword='zzz ai stem separation'")).scalar()
        assert now_trial == "trial"
        conn.execute(text("DELETE FROM discovery.dynamic_keywords WHERE keyword='zzz ai stem separation'"))
        conn.execute(text("DELETE FROM frontend.app_ratings WHERE user_email=:e"), {"e": EMAIL})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it PYTHONPATH=src .venv/bin/python -m pytest tests/discovery/test_mine_review_keywords.py -v`
Expected: FAIL (`No module named scripts.mine_liked_keywords`).

- [ ] **Step 3: Write the scripts**

```python
# scripts/mine_liked_keywords.py
"""Mine taste-aligned seed keywords from a user's liked apps' content. Proposed
phrases land as discovery.dynamic_keywords status='pending' (the sweep ignores
them) until promoted by scripts/review_mined_keywords.py.

Run:
    PYTHONPATH=src AGF_DATABASE_URL=postgresql://agf:agf@localhost:5433/agf \\
    .venv/bin/python scripts/mine_liked_keywords.py [user_email]
"""
import sys

from sqlalchemy import text

from agf.db.engine import begin, get_engine
from agf.discovery.liked_keyword_mining import dedup_candidates, propose_phrases

_LIKED = text(
    "SELECT m.advertiser_id, m.app_name AS name, a.description "
    "FROM frontend.app_ratings r "
    "JOIN frontend.emerging_scaling_apps m ON m.advertiser_id = r.advertiser_id "
    "LEFT JOIN public.apps a ON a.id = m.app_id "
    "WHERE r.user_email = :e AND r.rating = 'like'")

_COPY = text(
    "SELECT c.body_text FROM public.ads ad JOIN public.creatives c ON c.ad_id=ad.id "
    "WHERE ad.advertiser_id = :a AND c.body_text IS NOT NULL AND c.body_text <> '' "
    "ORDER BY ad.meta_rank ASC NULLS LAST, ad.last_seen DESC LIMIT 5")

_EXISTING = text("SELECT keyword FROM discovery.dynamic_keywords")

_INSERT = text(
    "INSERT INTO discovery.dynamic_keywords "
    "(keyword, source, status, kind, rationale, inspired_by, created_at) "
    "VALUES (:k, 'liked-mining', 'pending', 'app', :r, :ib, now()) "
    "ON CONFLICT (keyword, kind) DO NOTHING")


def _liked_apps(conn, user_email):
    apps = []
    for r in conn.execute(_LIKED, {"e": user_email}):
        copy = [b for (b,) in conn.execute(_COPY, {"a": r.advertiser_id})]
        apps.append({"advertiser_id": r.advertiser_id, "name": r.name or "",
                     "description": r.description or "", "ad_copy": copy})
    return apps


def mine(conn, user_email: str, *, propose_fn=propose_phrases) -> int:
    apps = _liked_apps(conn, user_email)
    if not apps:
        print(f"[mine] {user_email}: no liked apps", flush=True)
        return 0
    cands = propose_fn(apps)
    existing = {k for (k,) in conn.execute(_EXISTING)}
    fresh = dedup_candidates(cands, existing)
    for c in fresh:
        conn.execute(_INSERT, {"k": c.phrase, "r": c.rationale, "ib": c.inspired_by})
    print(f"[mine] {user_email}: proposed={len(cands)} new_pending={len(fresh)}", flush=True)
    return len(fresh)


def main() -> int:
    email = sys.argv[1] if len(sys.argv) > 1 else "founder@fielddesk.local"
    with begin(get_engine()) as conn:
        mine(conn, email)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

```python
# scripts/review_mined_keywords.py
"""Human-approval gate for taste-mined keywords. Lists pending phrases (with
rationale + inspired-by app names) and promotes approved ones to 'trial', which
admits them into the existing keyword flywheel.

Run (list):     PYTHONPATH=src AGF_DATABASE_URL=... .venv/bin/python scripts/review_mined_keywords.py
Run (promote):  ... scripts/review_mined_keywords.py --promote "ai stem separation"
"""
import sys

from sqlalchemy import text

from agf.db.engine import begin, get_engine

_PENDING = text(
    "SELECT keyword, status, rationale, inspired_by FROM discovery.dynamic_keywords "
    "WHERE source='liked-mining' AND status='pending' ORDER BY created_at DESC")

_PROMOTE = text(
    "UPDATE discovery.dynamic_keywords SET status='trial' "
    "WHERE keyword=:k AND status='pending'")


def list_pending(conn) -> list[dict]:
    return [{"keyword": r.keyword, "status": r.status, "rationale": r.rationale,
             "inspired_by": list(r.inspired_by or [])}
            for r in conn.execute(_PENDING)]


def promote(conn, keyword: str) -> int:
    return conn.execute(_PROMOTE, {"k": keyword}).rowcount


def main() -> int:
    with begin(get_engine()) as conn:
        if len(sys.argv) >= 3 and sys.argv[1] == "--promote":
            n = promote(conn, sys.argv[2])
            print(f"promoted {n} keyword(s) to trial", flush=True)
        else:
            for p in list_pending(conn):
                print(f"  {p['keyword']!r:40s}  {p['rationale']}  (apps={p['inspired_by']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it PYTHONPATH=src .venv/bin/python -m pytest tests/discovery/test_mine_review_keywords.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/mine_liked_keywords.py scripts/review_mined_keywords.py tests/discovery/test_mine_review_keywords.py
git commit -m "feat(discovery): mine + review scripts (pending->trial human gate)"
```

---

### Task 10: Live end-to-end + full suite + go-live

**Files:** none (operational verification).

**Interfaces:** consumes everything above against the real prod DB + real Gemini.

- [ ] **Step 1: Apply migrations to prod DB**

```bash
PYTHONPATH=src AGF_DATABASE_URL=postgresql://agf:agf@localhost:5433/agf .venv/bin/python -c "from agf.db.engine import get_engine; from agf.db.migrate import apply_migrations; print(apply_migrations(get_engine()))"
```
Expected: a list including `135_app_embeddings.sql`, `136_dynamic_keywords_taste.sql`.

- [ ] **Step 2: Embed the live corpus**

```bash
PYTHONPATH=src AGF_DATABASE_URL=postgresql://agf:agf@localhost:5433/agf .venv/bin/python scripts/embed_apps.py
```
Expected: `[embed] {'embedded': ~900+, 'skipped': ...}`. Re-run → `embedded: 0` (incremental).

- [ ] **Step 3: Score + eyeball ranking**

```bash
PYTHONPATH=src AGF_DATABASE_URL=postgresql://agf:agf@localhost:5433/agf .venv/bin/python scripts/score_content_preferences.py
docker exec db-postgres-1 psql -U agf -d agf -tAc "SELECT m.app_name, round(p.p_like::numeric,3), p.why FROM frontend.preference_scores p JOIN frontend.emerging_scaling_apps m USING (advertiser_id) WHERE p.user_email='founder@fielddesk.local' ORDER BY p.queue_rank LIMIT 15"
```
Expected: `model=content-sim-v1`; the top of the list trends toward the user's liked content (AI/creator-type), `why` reads `similar to <liked app>`. Sanity-check that faith/calorie-type apps are NOT at the top.

- [ ] **Step 4: Mine + review keywords live**

```bash
PYTHONPATH=src AGF_DATABASE_URL=postgresql://agf:agf@localhost:5433/agf .venv/bin/python scripts/mine_liked_keywords.py founder@fielddesk.local
PYTHONPATH=src AGF_DATABASE_URL=postgresql://agf:agf@localhost:5433/agf .venv/bin/python scripts/review_mined_keywords.py
```
Expected: pending phrases printed with rationale + inspired-by ids. Do NOT auto-promote — leave for the user to review.

- [ ] **Step 5: Full test suite (0 failed)**

Run bare (no pipe to grep — exit code must be read):
```bash
AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it PYTHONPATH=src .venv/bin/python -m pytest -q
```
Expected: `0 failed`. Confirm the summary shows no `skipped` for the new DB tests (a skip is not a pass). Fix any failure before proceeding.

- [ ] **Step 6: Restart the worker (picks up new stages)**

```bash
launchctl kickstart -k gui/$(id -u)/com.fielddesk.worker
```
Then confirm a cycle logs `[worker] embeddings:` and `[worker] preference scoring:`:
```bash
tail -n 50 ~/Library/Logs/fielddesk-worker.log
```

- [ ] **Step 7: Verify /review + "For you" in the browser**

Open `:3000/review` (login `founder@fielddesk.local` / `fielddesk`) and Discover → Sort "For you". Confirm the deck/order reflects content similarity and `why` strings render. (Go API + web need no changes — same `preference_scores` shape.)

- [ ] **Step 8: Final commit (any operational notes / memory)**

```bash
git add -- docs/  # only if you updated handoff/notes
git commit -m "docs: Spec 2 live — content preference + keyword mining operational"
```

---

## Self-Review

**Spec coverage:**
- Content embedding pipeline → Tasks 2,3,4. ✓
- Similarity-to-likes ranking → Tasks 5,6. ✓
- 6-axis Logit moved off serving → Task 7. ✓
- Keyword mining (cheap Gemini, pending gate, additive) → Tasks 8,9. ✓
- Storage `float4[]`/`REAL[]`, no pgvector → Task 1. ✓
- Migrations 135/136 incl. status-check extension → Task 1. ✓
- Worker wiring `AGF_EMBED_APPS` + `AGF_SCORE_PREFERENCES` → Task 6. ✓
- Error handling (best-effort stages, dim mismatch raises pre-write, cold-start) → Tasks 3,5,6. ✓
- Testing (pure unit + mocked Gemini + live probe + IT + full suite) → every task + Task 10. ✓
- Out-of-scope items (triage prior, corpus neighbor re-rank, funnel sharpening, loop weighting, pgvector) → not implemented. ✓

**Type consistency:** `embed_texts`/`embed_apps`/`embed_fn` names align across Tasks 3,4,6. `ContentPreferenceModel.fit(liked, disliked)` signature + `(advertiser_id, name, vec)` tuple shape consistent across Tasks 5,6. `Candidate(phrase, rationale, inspired_by)` + `propose_phrases`/`dedup_candidates`/`mine`/`promote` consistent across Tasks 8,9. `score_all_users(conn) -> dict` matches the worker call site (Task 6) and the existing worker contract.

**Placeholder scan:** no TBD/TODO; every code step shows complete code; every run step shows the command + expected output.
