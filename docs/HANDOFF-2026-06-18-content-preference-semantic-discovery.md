# Handoff — content-based preference model + semantic discovery (Spec 2)

Written 2026-06-18 to resume in a fresh session. Everything below is on `main`
(solo local repo, commit straight to main). Worker + Go API run on this Mac.

**TL;DR of where we are:** the per-user preference model (Spec 1) shipped and is
live — `/review` deck + Discover "For you" sort, both ranked by a statsmodels
logistic regression over 6 hand-engineered axes. The user (correctly) wants to
replace those hand-picked axes with the app's **actual content** (what it does,
the problem it solves, category, ad copy) via **embeddings + a real ML model**,
and ultimately use those embeddings to **steer scraping** toward apps they like.
We were mid-brainstorm on that when we paused. **Resume the brainstorm → spec →
plan for Spec 2.**

---

## 1. What shipped this session (all on `main`, live, verified)

The whole **Spec 1 preference-ranking system** — 20 commits `0630f43`..`edf6e4b`
(see `git log`). Highlights:

| Piece | Commits | Notes |
|---|---|---|
| Preference model (pure Python) | `b52807a`,`03a09da`,`eb20196`,`0630f43` | statsmodels Logit, like-vs-rest, L1, 6 features, cold-start fallback, 75/25 interleave, why-string. `src/agf/scoring/preference.py` |
| Scorer + worker wiring | `a285705`,`79a598c` | `scripts/score_preferences.py` → `frontend.preference_scores`; worker re-fits each cycle (`AGF_SCORE_PREFERENCES`, default on, after `run_scoring`) |
| Go endpoint | `cd76fa2`,`5c8a570` | `GET /api/preference-queue?user_email=&limit=` (unrated, ranked by queue_rank, hero creative) |
| Web `/review` deck | `35395a6`,`f22b70a`,`0f6c65e`,`05dcb8a` | card deck, keyboard ←/↓/→, reuses `setRating`; removed legacy `/review→/saved` redirect; gated behind auth |
| Review card = gem-style info card | `83ad53c`,`16f9cc7`,`d7230e5`,`992637c`,`b9c6406` | small logo, real table facts, **honest funnel domain** (shows `appstack.link` store-bounce vs real funnel), `AdGrowth` diagram, media-only ad thumbnails, store screenshots w/ click-to-zoom **lightbox (←/→/Esc)** |
| Continuous review | `c492230` | fetch the whole unrated queue, no page size, no counter |
| **"For you" Discover sort** | `2da8254` | Sort dropdown option ordering by `p_like` (correlated subquery, `user_email` param, 422 if missing). `shortlist_repo.go` `buildShortlistOrder` + `foryou` in `ValidSort` |
| Review nav tab | `edf6e4b` | header: Discover → **Review** → Saved |
| Taxonomy prune | `54476a4` | dropped `_NICHE_SPIRITUAL` (faith/astrology/tarot — 0 likes) + calorie/macro/fasting/weight-loss tracker seeds; retired matching `discovery.dynamic_keywords` |

Spec + plan for Spec 1: `docs/superpowers/specs/2026-06-18-review-page-preference-ranking-design.md`,
`docs/superpowers/plans/2026-06-18-review-page-preference-ranking.md`.

**Parallel scraping work also landed this session** (another track): `a76b391`
(App Store new-releases advertiser expansion), `22832b3` (flywheel promote bar
3→1). See `docs/research/2026-06-18-keyword-seed-design.md` and
`docs/scraping-explained-grug.html` + memory `keyword-seed-redesign-2026-06-18`.

## 2. Live system state / how to operate

- **DB:** Postgres docker `db-postgres-1`, `localhost:5433`, user `agf`. Prod `agf`, test `agf_it`.
  `docker exec db-postgres-1 psql -U agf -d agf -c "..."`.
- **Worker:** launchd `com.fielddesk.worker` (KeepAlive). Restart after code change:
  `launchctl kickstart -k gui/$(id -u)/com.fielddesk.worker`. It holds code in-memory.
- **Go API:** launchd `com.fielddesk.api`, `:8000`. Deploy:
  `cd backend && go build -o ~/bin/agf-api . && launchctl kickstart -k gui/$(id -u)/com.fielddesk.api`.
- **Web:** Next 16 (custom — read `web/AGENTS.md` + `web/node_modules/next/dist/docs/` before new Next APIs).
  Dev server on `:3000` (started this session via `nohup npm run dev`). Login `founder@fielddesk.local` / `fielddesk`.
  **Browser quirk:** the old permanent `/review→/saved` 308 may be cached — append `?v=N` to bust it (the in-app nav link is fine).
- **Tests:** Python DB tests SKIP silently without `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it`
  (a skip is NOT a pass — grep summary). Go uses `AGF_IT_DB_URL` (same DSN); always `-count=1` (cached green burned us).
- **statsmodels gotcha:** `import statsmodels.api` is BROKEN on this env (py3.13/sm0.14.4 — upstream `deprecate_kwarg`
  bug). `preference.py` imports `statsmodels.discrete.discrete_model.Logit` directly and monkey-patches
  `cov_params_func_l1` (pinv vs inv) to survive perfectly-separable data. Documented in-file.

## 3. The preference model today (Spec 1) — what we're upgrading

- **Model:** per-user `statsmodels.Logit` (like=1, neutral/dislike=0), L1-regularized, on **6 engineered axes**:
  `has_funnel, whitespace, funnel_intent, scaling_velocity, under_radar, demand` (standardized; `gem_score`
  deliberately excluded to avoid double-counting). `p_like = sigmoid(β₀ + Σ coef·standardized_feature)`.
- **Calculation** (worked example in conversation): intercept β₀ ≈ −2.894 (encodes ~8% base like-rate);
  each feature standardized then × its coefficient; sigmoid → `p_like`. Max `p_like` ≈ 0.55 (low base + many
  disliked apps share decent features).
- **Storage:** `frontend.preference_scores(user_email, advertiser_id, p_like, uncertainty, queue_rank, why,
  model_version, scored_at)` PK `(user_email, advertiser_id)` (migration 133). Worker re-fits each cycle.

### 455-label learnings (snapshot 2026-06-18) — see memory `preference-learnings-455-labels-2026-06-18`
- **455 ratings: 36 like / 156 neutral / 263 dislike** (~8% like-rate).
- Coefficients 134→455: **demand 0.27→0.94 (now #1)**, has_funnel 0.79, scaling_velocity ~0→0.29,
  funnel_intent 0.22, whitespace 0.17, under_radar 0.13.
- Funnel still **2.7× lift** (16% like with funnel vs 5.8% without).
- **GemScore mis-ranks**: 23/36 likes are in the **<60** band → likes live where GemScore is low. The
  coefficients double as the GemScore-reweight proposal (raise demand + funnel-intent).

## 4. NEXT — Spec 2: content-based preference + semantic discovery (DESIGN IN PROGRESS)

**The user's directive (verbatim intent):** stop using the 6 hand-engineered axes as "hard rules." They
discard *what the app actually is*. Instead featurize the **actual content** — what it does, the problem it
solves, category, store description, ad copy — and use a **real ML model** so it learns concepts like "AI
creator tools yes / faith + calorie trackers no" directly from meaning. Then use the same representation to
**steer scraping** (find apps semantically similar to likes, not just keyword matches).

### Why the current model is limited (agreed)
"AI Vocal Remover" and "calorie tracker" can look near-identical on the 6 axes despite being semantically
opposite. The model can't see content or category, so it can't learn the user's real taste — only axis proxies.

### Grounded facts for the design (checked live this session)
- Content available: **976/981 apps have store descriptions**; **50,756/66,641 creatives have ad copy
  (`public.creatives.body_text`)**; `tagline`, category/genre, funnel destination all present.
  `apps.description` = migration 103.
- **No infra yet:** no `pgvector` extension, no embedding lib, no `sklearn`, no torch in `requirements.txt`
  (only numpy/scipy/statsmodels/pandas). So embeddings = new local model (heavy: torch/sentence-transformers)
  OR an API. **Gemini is already used** (Go explainers, `GEMINI_API_KEY`) → Gemini embeddings from Python is
  the no-heavy-dep path. At ~1,000 apps, **numpy handles 1k×768 similarity trivially — pgvector NOT needed.**

### Open decisions to resolve in the brainstorm (resume HERE)
1. **Scope (the decomposition question we were on):**
   - **(A) recommended** — build the embedding pipeline + the content-based ranking model FIRST (powers
     `/review` + "For you"); semantic discovery becomes the next spec that consumes the embeddings. Mirrors
     Spec 1→2, ships value fast, de-risks the embedding choice before wiring it into the expensive scraper.
   - **(B)** — one big spec, ranking + discovery together (more complete, riskier, couples model + scraper).
   - User said "yeah" to kicking off the design; lead by confirming **A vs B** (recommend A).
2. **Embedding source:** Gemini embeddings API (recommended — reuse key, no heavy dep, small cost) vs local
   sentence-transformers (free/offline but pulls torch ~GBs) vs OpenAI/Voyage (new key).
3. **Model approach over embeddings (455 labels / 36 likes is still smallish → overfitting risk):**
   - **similarity-to-likes** (centroid of liked − disliked, cosine rank) — robust few-shot, no overfit, no probability;
   - **regularized classifier** (logistic on PCA-reduced embeddings, or grad-boost) — needs sklearn or careful L2 + dim-reduction;
   - **hybrid** — concat [reduced embedding + structured: category, demand, funnel-kind, has-real-funnel] → regularized classifier.
   - Recommendation to float: prototype **similarity-to-likes first** (cheap, few-shot-robust), compare to a regularized classifier.
4. **Keep the 6-axis model?** Yes — keep it for the **GemScore-tuning** knob (interpretable coefficients);
   the content model powers the personal feed ranking. Two models, two jobs. (Embeddings aren't interpretable.)
5. **What content to embed:** name + tagline + store description + top ad copy (+ category as structured feature).
6. **Storage/recompute:** embedding per app (JSONB/float[] column or a side table; numpy in Python — no pgvector
   at this scale); recompute on new/changed apps; the preference score still writes to `preference_scores`.

### Spec 2b (the prize, after the ranking model) — semantic discovery
Use the same content embeddings to steer scraping toward the user's taste. Levers (from the earlier strategy
discussion + memory `keyword-seed-redesign-2026-06-18`):
1. **Mine keywords from LIKED apps' copy/funnels** — positive-label-driven keyword discovery (changes *what's found*).
2. **Triage prior** — spend the metered enrich budget on candidates predicted-likeable (cheap pre-enrich signals:
   real-funnel destination, niche; demand only post-enrich).
3. **Sharpen funnel detection** — real web2app landing vs store/MMP bounce (appstack/AppsFlyer/Branch → not a funnel);
   make `has_funnel` mean a *real* funnel.
4. **Close the keyword loop** — weight `discovery.dynamic_keywords` by the like-rate of apps they surface; auto-retire losers (keep an explore budget).
5. **Semantic neighbor search** — find apps whose content embeddings are near the liked centroid.
**Carry the caution:** a preference signal in discovery is a stronger filter bubble than in the feed — reserve a
discovery explore budget so new niches keep surfacing.

## 5. Quick-start for the next session
1. Read this file + `MEMORY.md` (esp. `preference-learnings-455-labels`, `preference-ranking-northstar`,
   `keyword-seed-redesign`).
2. Confirm worker + API up: `launchctl list | grep fielddesk`; API on `:8000`.
3. Re-run `AGF_DATABASE_URL=postgresql://agf:agf@localhost:5433/agf PYTHONPATH=src .venv/bin/python scripts/score_preferences.py`
   for the latest coefficients (user keeps rating).
4. Resume the **brainstorm**: confirm scope **A vs B** (§4 decision 1), then embedding source, then model
   approach → write `docs/superpowers/specs/2026-06-NN-content-preference-design.md` → plan → build
   (subagent-driven worked well: implementer per task on cheap/standard model, sonnet reviewer, opus final review).
