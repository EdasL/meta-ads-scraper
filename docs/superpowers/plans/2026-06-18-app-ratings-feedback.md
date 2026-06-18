# App Ratings Feedback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user rate apps like/neutral/dislike + comment inline in the Discover table, persisted as a labeled dataset Claude analyzes to tune scoring/scraping.

**Architecture:** New `frontend.app_ratings` table (per user_email + advertiser_id, with a JSONB feature snapshot). Two Go endpoints mirroring saved-gems. An inline `RatingControl` in the GemTable. A Python analysis script that aggregates ratings by trait for Claude to act on.

**Tech Stack:** Postgres (raw SQL migration), Go (Gin + GORM-raw, mirror `saved-gems`), Next.js/React (web/src), Python (analysis script + pytest).

## Global Constraints

- Product copy is plain, clear, professional — no marketing/novelty voice (CLAUDE.md).
- DB tests SKIP silently without `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it` — set it and grep pytest summary for "skipped" before trusting green.
- Go IT DB env var is `AGF_IT_DB_URL`; Go API build = build → `mv ~/bin/agf-api` → `launchctl kickstart com.fielddesk.api`.
- Single-user (user_email, like saved_gems). Comment optional. `rating:"none"` un-rates (deletes).
- Migrations: next number is **132**; applied in lexical order by `agf.db.migrate.apply_migrations`.

---

### Task 1: DB migration — `frontend.app_ratings`

**Files:**
- Create: `db/schema/132_app_ratings.sql`
- Test: `tests/db/test_app_ratings.py`

**Interfaces:**
- Produces: table `frontend.app_ratings (user_email TEXT, advertiser_id BIGINT, rating TEXT, comment TEXT, features JSONB, rated_at TIMESTAMPTZ, updated_at TIMESTAMPTZ)`, PK `(user_email, advertiser_id)`, CHECK `rating IN ('like','neutral','dislike')`.

- [ ] **Step 1: Write the migration**

```sql
-- db/schema/132_app_ratings.sql
-- Per-user like/neutral/dislike + comment on a directory app. A labeled dataset
-- Claude analyzes to tune GemScore / vetoes / scraping. features = a snapshot of the
-- app's scoring signals at rating time (scoring runs 24/7, so the live row drifts).
CREATE TABLE IF NOT EXISTS frontend.app_ratings (
    user_email    TEXT        NOT NULL,
    advertiser_id BIGINT      NOT NULL,
    rating        TEXT        NOT NULL CHECK (rating IN ('like','neutral','dislike')),
    comment       TEXT        NOT NULL DEFAULT '',
    features      JSONB       NOT NULL DEFAULT '{}'::jsonb,
    rated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_email, advertiser_id)
);
CREATE INDEX IF NOT EXISTS idx_app_ratings_rating ON frontend.app_ratings (rating);
```

- [ ] **Step 2: Write the failing test**

```python
# tests/db/test_app_ratings.py
from sqlalchemy import text

def test_app_ratings_table_and_check(pg_conn):
    pg_conn.execute(text(
        "INSERT INTO frontend.app_ratings (user_email, advertiser_id, rating, comment) "
        "VALUES ('u@x.com', 1, 'like', 'nice')"))
    row = pg_conn.execute(text(
        "SELECT rating, comment, features FROM frontend.app_ratings "
        "WHERE user_email='u@x.com' AND advertiser_id=1")).one()
    assert row.rating == 'like' and row.comment == 'nice' and row.features == {}
    # CHECK rejects a bad rating
    import pytest, sqlalchemy
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        pg_conn.execute(text(
            "INSERT INTO frontend.app_ratings (user_email, advertiser_id, rating) "
            "VALUES ('u@x.com', 2, 'love')"))
```

- [ ] **Step 3: Run test to verify it fails** — `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it pytest tests/db/test_app_ratings.py -q` → FAIL (relation does not exist).

- [ ] **Step 4: Apply migration to both DBs**

```bash
for DB in agf_it agf; do AGF_DATABASE_URL=postgresql://agf:agf@localhost:5433/$DB PYTHONPATH=src .venv/bin/python -c "from agf.db.engine import get_engine; from agf.db.migrate import apply_migrations; print(apply_migrations(get_engine()))"; done
```

- [ ] **Step 5: Run test to verify it passes** — same pytest command → PASS.

- [ ] **Step 6: Commit** — `git add db/schema/132_app_ratings.sql tests/db/test_app_ratings.py && git commit -m "feat(ratings): app_ratings table + migration 132"`

---

### Task 2: Go API — ratings endpoints (mirror saved-gems)

**Files:**
- Read first (the pattern to mirror): `backend/repositories/saved_gems_repo.go`, `backend/controllers/saved.go`, `backend/models/models.go`, `backend/routers/routers.go`.
- Create: `backend/repositories/ratings_repo.go`, `backend/controllers/ratings.go`
- Modify: `backend/models/models.go` (add `RatingIn`, `RatingRow`), `backend/routers/routers.go` (add routes)
- Test: `backend/repositories/ratings_repo_test.go` (mirror saved_gems repo test if present)

**Interfaces:**
- Produces:
  - `repositories.UpsertRating(db, userEmail string, advertiserID int64, rating, comment string) error` — upsert; snapshot `features` from `frontend.emerging_scaling_apps` (LEFT JOIN `public.advertisers` for page_like_count) into JSONB; sets `updated_at=now()`, keeps `rated_at` on conflict.
  - `repositories.DeleteRating(db, userEmail string, advertiserID int64) error`
  - `repositories.ListRatings(db, userEmail string) (map[int64]RatingRow, error)`
  - Routes: `POST /api/ratings/:advertiser_id`, `GET /api/ratings`.
  - `rating == "none"` on POST → DeleteRating.

- [ ] **Step 1:** Read `saved_gems_repo.go` + `saved.go` + the saved routes in `routers.go` to copy the exact db-access, error, and binding patterns.

- [ ] **Step 2: Write the repo upsert with feature snapshot**

```go
// backend/repositories/ratings_repo.go  (mirror saved_gems_repo.go conventions)
func UpsertRating(db *gorm.DB, userEmail string, advertiserID int64, rating, comment string) error {
    return db.Exec(`
        INSERT INTO frontend.app_ratings (user_email, advertiser_id, rating, comment, features)
        SELECT ?, ?, ?, ?, to_jsonb(f) FROM (
          SELECT m.gem_score, m.scaling_velocity, m.under_radar, m.funnel_intent,
                 m.demand, m.whitespace, m.active_ad_count, m.cohort, m.installs,
                 m.rating_count, adv.page_like_count,
                 (m.funnel_url IS NOT NULL) AS has_funnel
          FROM frontend.emerging_scaling_apps m
          LEFT JOIN public.advertisers adv ON adv.id = m.advertiser_id
          WHERE m.advertiser_id = ?
        ) f
        ON CONFLICT (user_email, advertiser_id) DO UPDATE SET
          rating = EXCLUDED.rating, comment = EXCLUDED.comment,
          features = EXCLUDED.features, updated_at = now()`,
        userEmail, advertiserID, rating, comment, advertiserID).Error
}
```
(If the matview row is absent the INSERT inserts zero rows — acceptable; only rate visible apps. `DeleteRating` = `db.Exec("DELETE FROM frontend.app_ratings WHERE user_email=? AND advertiser_id=?", ...)`. `ListRatings` = `SELECT advertiser_id, rating, comment` → map.)

- [ ] **Step 3:** Add `RatingIn{UserEmail, Rating, Comment}` and `RatingRow{Rating, Comment}` to `models.go`; add the controller (`ratings.Save`, `ratings.List`) mirroring `saved.go` (bind JSON, validate rating in like/neutral/dislike/none, call repo); register the two routes in `routers.go`.

- [ ] **Step 4: Write the repo test** (mirror the saved_gems repo test): seed an advertiser + a matview row, `UpsertRating` like + comment, assert `ListRatings` returns it and `features->>'gem_score'` is populated; `UpsertRating` again with dislike → overwrites; `DeleteRating` → gone. Run with `AGF_IT_DB_URL`.

- [ ] **Step 5: Build + run Go tests**

```bash
cd backend && AGF_IT_DB_URL=postgresql://agf:agf@localhost:5433/agf_it go test ./... 2>&1 | tail -20
```
Expected: PASS.

- [ ] **Step 6: Commit** — `git add backend/ && git commit -m "feat(ratings): GET/POST /api/ratings (mirror saved-gems)"`

---

### Task 3: Web — inline RatingControl in the GemTable

**Files:**
- Read first: `web/src/components/discover/GemTable.tsx`, `web/src/components/discover/DiscoverFeed.tsx` (or wherever the feed fetch lives), the SaveToggle component, and `web/src/lib/api.ts` (the live adapter).
- Create: `web/src/components/discover/RatingControl.tsx`
- Modify: `GemTable.tsx` (new cell), the feed component (fetch `/api/ratings`, pass rating down), `lib/api.ts` (add `getRatings`, `setRating`).

**Interfaces:**
- Consumes: `GET /api/ratings` → `{[advertiserId]: {rating, comment}}`; `POST /api/ratings/{id}` `{user_email, rating, comment}`.
- Produces: `<RatingControl advertiserId rating comment onChange />` — 👍/➖/👎 + note toggle + comment textarea; optimistic, reverts on failure.

- [ ] **Step 1:** Read GemTable + the feed fetch + SaveToggle + api.ts to mirror data-loading, auth (user_email/token), and styling tokens.

- [ ] **Step 2:** Add `getRatings()` and `setRating(advertiserId, rating, comment)` to `lib/api.ts` (mirror the saved-gems calls, same auth header / user_email).

- [ ] **Step 3:** Create `RatingControl.tsx`: three buttons (active state highlighted via existing tokens), a note icon toggling a textarea that calls `setRating` on blur/Enter; optimistic local state, revert + console/toast on error. Plain copy.

- [ ] **Step 4:** In the feed component, fetch ratings once on mount; pass `rating`/`comment` per row into a new GemTable column rendering `<RatingControl>`. Clicking a rating updates local map.

- [ ] **Step 5: Verify** — `cd web && npm run build` (or `lint`/`typecheck`) passes; then run the app and manually rate a row, reload, confirm it persists (the `verify` skill / run skill). Capture the working behavior.

- [ ] **Step 6: Commit** — `git add web/ && git commit -m "feat(ratings): inline like/neutral/dislike + comment in GemTable"`

---

### Task 4: Analysis script — ratings → trait breakdown

**Files:**
- Create: `scripts/analyze_ratings.py`
- Test: `tests/test_analyze_ratings.py`

**Interfaces:**
- Produces: `analyze_ratings(conn) -> dict` — `{by_cohort, by_score_band, by_funnel, by_incumbent, totals}` from `frontend.app_ratings.features`; plus a `main()` that prints a readable report. Pure aggregation; mutates nothing.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_analyze_ratings.py
from sqlalchemy import text
from scripts.analyze_ratings import analyze_ratings

def test_analyze_ratings_breaks_down_by_trait(pg_conn):
    rows = [("u@x.com", 1, "dislike", '{"cohort":"Games","gem_score":72,"has_funnel":false}'),
            ("u@x.com", 2, "dislike", '{"cohort":"Games","gem_score":68,"has_funnel":false}'),
            ("u@x.com", 3, "like",    '{"cohort":"Health","gem_score":81,"has_funnel":true}')]
    for ue, aid, r, f in rows:
        pg_conn.execute(text("INSERT INTO frontend.app_ratings (user_email,advertiser_id,rating,comment,features) "
                             "VALUES (:ue,:aid,:r,'',CAST(:f AS jsonb))"),
                        {"ue": ue, "aid": aid, "r": r, "f": f})
    out = analyze_ratings(pg_conn)
    assert out["totals"] == {"like": 1, "neutral": 0, "dislike": 2}
    assert out["by_cohort"]["Games"]["dislike"] == 2
    assert out["by_funnel"]["with_funnel"]["like"] == 1
```

- [ ] **Step 2: Run to verify it fails** — `AGF_TEST_DB_URL=... pytest tests/test_analyze_ratings.py -q` → FAIL (import error).

- [ ] **Step 3: Implement** `analyze_ratings(conn)` (read rating + features JSONB, bucket counts by cohort / score band (<60,60-70,70-80,80+) / has_funnel / incumbent signal page_like_count>10k) and a `main()` that opens an engine from `AGF_DATABASE_URL` and prints the report. Read-only.

- [ ] **Step 4: Run to verify it passes** — same pytest → PASS.

- [ ] **Step 5: Commit** — `git add scripts/analyze_ratings.py tests/test_analyze_ratings.py && git commit -m "feat(ratings): analyze_ratings trait breakdown for tuning"`

---

## Self-Review

- **Spec coverage:** app_ratings table (T1) ✓; POST/GET endpoints + snapshot (T2) ✓; inline RatingControl + GemTable + client merge (T3) ✓; analyze_ratings.py report (T4) ✓; single-user/comment-optional/none-deletes (T1 schema + T2 logic) ✓; Claude-in-the-loop (T4 is read-only, no auto-mutation) ✓.
- **Placeholders:** none — each task has concrete SQL/Go/Python and exact commands. Go controller/web steps reference the exact pattern files to mirror (saved-gems / SaveToggle) because the codebase convention, not invented code, is the source of truth there.
- **Type consistency:** `UpsertRating/DeleteRating/ListRatings`, `RatingIn/RatingRow`, `getRatings/setRating`, `analyze_ratings` used consistently across tasks.
