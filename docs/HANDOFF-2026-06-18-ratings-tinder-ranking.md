# Handoff — ratings feedback, learnings, and the Tinder review page

Written 2026-06-18 to continue in a fresh session. Everything below is on `main`
(solo repo, no remote — commit straight to main per workflow). The live worker +
Go API are running on this Mac.

---

## 1. What shipped this session (all on `main`, verified live)

| Feature | Commits | Notes |
|---|---|---|
| Funnel **surfacing** fix (write-gate) | `ad44687` | enrich now records any web-to-app landing as a funnel, not only confirmed quizzes. Feed funnel links 59→94. |
| Funnel **discovery breadth** | `2f9473e`, `85d53c3` | depth (funnel sweep 3→8 pages), bridge (app-backed funnels → app feed), funnel keyword flywheel (migration 131). |
| **24/7 continuous** scraping | `80184e2` | funnel stage + both flywheels now run every tick (continuous mode), not once/day. Worker KeepAlive = always up. |
| **App ratings** (like/neutral/dislike + comment) | `15bffc8`..`1f45fda` | the feedback feature — see §3. |
| **Per-page** param on Discover | `f4299a2` | `?per=` URL param, 24/48/96 selector in the pager. |

Specs/plans under `docs/superpowers/specs|plans/2026-06-18-*`. Memory files updated
under `~/.claude/projects/.../memory/` (funnel-write-gate-fix, funnel-discovery-breadth,
app-ratings-feedback, etc.).

## 2. Live system state / how to operate

- **DB:** Postgres in docker container `db-postgres-1`, `localhost:5433`, user `agf`.
  Prod DB = `agf`, test DB = `agf_it`. Query: `docker exec db-postgres-1 psql -U agf -d agf -c "..."`.
- **Worker:** launchd `com.fielddesk.worker` (KeepAlive, auto-restarts; pid changes).
  Logs `~/Library/Logs/fielddesk-worker.log`. Runs app+funnel discovery + flywheels +
  enrichment 24/7. Proxy = DataImpulse residential (metered ~$1/day; in the worker plist
  `AGF_PROXIES`). Restart after deploy: `launchctl kickstart -k gui/$(id -u)/com.fielddesk.worker`.
- **Go API:** launchd `com.fielddesk.api`, binary `~/bin/agf-api`, serves `:8000`, DB `agf`.
  Deploy: `cd backend && go build -o ~/bin/agf-api . && launchctl kickstart -k gui/$(id -u)/com.fielddesk.api`.
- **Web:** Next.js (CUSTOM Next 16 — see `web/AGENTS.md`, read node_modules/next/dist/docs
  before new Next APIs). Dev server was running on `:3000`. Web user = `founder@fielddesk.local`.
- **Tests:** Python DB tests SKIP SILENTLY without `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it`
  — always set it and grep summary for "skipped". Go tests use `AGF_IT_DB_URL` (same DSN).
  2 pre-existing dry-run worker test failures (`FakeExtractor.search_listing`) are UNRELATED.
- **VERIFICATION LESSON (this session):** a subagent falsely reported a Go test passing
  (it was a *cached* result + an FK-unsafe test seed that only passed once on a clean DB).
  ALWAYS independently re-run with `go clean -testcache` / `-count=1` before trusting green.

## 3. The ratings feature (already built — this is the data source)

- **Table `frontend.app_ratings`** (migration 132): PK (user_email, advertiser_id),
  `rating` CHECK like/neutral/dislike, `comment`, `features` JSONB **snapshot** (frozen at
  rating time: gem_score, scaling_velocity, under_radar, funnel_intent, demand, whitespace,
  active_ad_count, cohort, installs, rating_count, page_like_count, has_funnel), rated_at, updated_at.
- **Go API:** `POST /api/ratings/{advertiser_id}` {user_email,rating,comment} (rating="none"
  deletes; snapshots features); `GET /api/ratings?user_email=`. backend/{repositories,controllers}/ratings*.
- **Web:** inline 👍/➖/👎 + ✏️note control in the Discover GemTable (`RatingControl.tsx`).
  The user finds the TABLE hard to review in — hence the Tinder page (§5).
- **Analysis:** `scripts/analyze_ratings.py` — `analyze_ratings(conn)` → totals + breakdown
  by cohort / score-band / funnel / incumbent. Run:
  `PYTHONPATH=src AGF_DATABASE_URL=postgresql://agf:agf@localhost:5433/agf .venv/bin/python scripts/analyze_ratings.py`
- Read raw ratings + comments:
  `docker exec db-postgres-1 psql -U agf -d agf -c "SELECT r.rating, m.app_name, r.features->>'cohort', r.comment FROM frontend.app_ratings r LEFT JOIN frontend.emerging_scaling_apps m ON m.advertiser_id=r.advertiser_id ORDER BY r.rating"`

## 4. LEARNINGS from the 134 ratings so far (18 like / 57 neutral / 59 dislike)

The comments are unusually explicit; these signals are robust (appear across many comments):

1. **Funnel presence is the #1 LIKE driver — and GemScore barely weights it.**
   With-funnel: **30% like / 27% dislike.** No-funnel: **8% like / 50% dislike.** Every *like*
   comment mentions funnel ("clear funnel", "missing the quiz funnel", "Nice + funnel");
   the top *neutral* reason is "no funnel". **GemScore has Funnel Intent at `^0.03` (≈0).**
   → biggest tuning lever: raise FI weight a lot.
2. **"Saturated market" is the #1 DISLIKE driver — also barely weighted.** Constant in
   comments. That's the **Whitespace** axis, currently `^0.05`. → raise W weight.
3. GemScore is **mis-calibrated for this user**: likes are spread across all score bands
   (5 in <60, 8 in 60-70, 5 in 70-80) because the two axes the user cares about are
   under-weighted. (Formula lives in `src/agf/scoring/gemscore.py`; weights are hardcoded dicts.)

**Bugs the ratings surfaced (fixes, not tuning):**
- **Duplicates** — hard-dislike pattern ("duplicate, we already have this" ×6) AND literal
  duplicate rows (PCOS Pal has 2 advertiser pages). Need **app-level dedup** in the feed
  (known gap: multiple advertiser pages → same app).
- **Wrong links** — "links to traveltexas somehow" (Tastemade); "ads don't match the app" (QR Link).
- **Fake funnels** — "quiz funnel loads and leads straight to the app store" (DLOOK). Some
  detected funnels are store-bounces, not real quiz funnels → distinguish `kind=install` vs
  `subscribe/unknown` (funnel_confirm already stores `kind`).
- **Non-digital / physical products** slip in — "physical product not digital" (Casedrops, scored 79),
  "not even an app" (NCLEX). Candidate veto.

Caveat: 134 is enough for these cross-cutting signals; per-category numbers are still thin.

## 5. NEXT: Tinder-style review page + preference ranking (DESIGN IN PROGRESS — not built)

User wants a **one-app-at-a-time "Tinder" review page** (the table is hard to review in),
and asked whether an open-source algorithm can order it to show stuff they like more.

**Resolved design decisions:**
- It's **content-based preference learning**, NOT collaborative filtering (single user — CF
  needs many users). Learn from the user's labels which app *features* they like.
- **Model: logistic regression on the `app_ratings.features` snapshot** → P(like). Use
  **statsmodels** (`Logit`) — ALREADY a dependency (numpy/scipy/statsmodels in requirements.txt;
  NO new dep, NO sklearn needed). Re-fit as labels grow.
- **Bonus:** LR coefficients are interpretable and **double as the GemScore-tuning proposal**
  (they quantify how much funnel/whitespace/etc. drive the user's preference). One model, two jobs.
- **Must blend explore/exploit** — pure "show what you'll like" creates a filter bubble,
  starves the model of corrective signal, and stops surfacing sleepers. Cold-start (until
  ~30-50 labels): fall back to a funnel+whitespace heuristic.
- Neutral handling = weak negative or exclude (design choice, TBD).

**OPEN QUESTION the user was about to answer (ask again next session):** what should the
Tinder feed optimize for —
- **Balanced (recommended):** ~70-80% best-first + 20-30% uncertain/diverse.
- **Best-first (exploit):** always highest predicted-like (bubbles, learns slower).
- **Learning-first (explore):** prioritize the most informative/uncertain labels (tunes fastest).

**Also undecided (next-session brainstorm):** page layout (full-screen card with creative +
ad count + growth + funnel link + L/N/D buttons + keyboard shortcuts), whether to reuse the
dossier data, how the queue is served (new Go endpoint ranking the matview by the model's
P(like), or rank client-side from a fetched page), where the model runs (a Python script that
writes a per-advertiser score the API reads, vs scoring at request time).

**Suggested next-session flow:** brainstorm → spec → plan → build (subagent-driven worked well).
Start by re-asking the explore/exploit question, then design the page + the ranking endpoint.

## 6. Quick-start for the next session
1. Read this file + the memory index (`MEMORY.md`).
2. Confirm worker + API are up: `launchctl list | grep fielddesk`; API on `:8000`.
3. Re-run `scripts/analyze_ratings.py` for the latest label counts (user keeps rating).
4. Pick up §5: explore/exploit decision → Tinder page design.
