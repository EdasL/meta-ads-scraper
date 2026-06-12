# Handoff — Funnel discovery pipeline (2026-06-12)

**For a fresh session.** Goal: find **web quiz / web2app funnels** ("take the quiz → personalized plan
→ install the app / start a subscription") and put them into a tracked pool — a sibling to the existing
apps-only discovery pipeline. Engine 1 (discover) + Engine 2 v1 (confirm/label) are **built, tested, and
live-validated**. Next milestone is **surfacing** (score + a feed/API), then continuous accrual.

Read these memories first (they auto-load): `funnel-discovery-2026-06-12`, `discover-product-eval-2026-06-12`,
`gemscore-v2-2026-06-11`. Design/research docs: `docs/research/2026-06-11-funnel-discovery-method.md`
(the method + live validation + signal→detection mapping) and the two plans under `docs/superpowers/plans/`.

---

## Where things stand

**Branch:** `feat/funnel-discovery` (off `main` @ `03392e5`), **unmerged**. ~18 commits: research docs →
Engine 1 → Engine 2. Nothing merged to main yet. The working tree also has unrelated parallel-session WIP
(`src/agf/workers/main.py`, `tests/workers/test_main.py`, screenshots) — **do not commit those**; every funnel
commit added only its own files.

**Tests:** 27 funnel tests green. Full suite = 389 passed + **2 pre-existing, unrelated failures**
(`test_sweep_run_gate` = shared `agf_it` DB already has sweep_runs finished today; `test_main` = whois needs
network). Both reproduce on `main` — not ours.

**Live data:** `discovery.funnel_candidates` holds **72 real funnels** (from two bounded live sweeps).
After Engine 2: **2 install, 34 unknown(confirmed), 9 dropped(generic web)**; **27 still `status='new'`**
(run the confirm script again to finish labeling them).

**NOT running continuously.** The app pipeline runs continuously via launchd (`com.fielddesk.worker` →
`run_sweep`/`triage`/`enrich`). The funnel pipeline is **manual scripts only** — not wired to the worker, no
cron. That's deliberate (decided: surface funnels first, then turn on continuous).

---

## Architecture (the seam)

Two sibling pipelines under `src/agf/discovery/`, sharing only **pure primitives**; the app pipeline
(`sweep.py`→`triage.py`, `discovery.candidates`) is **untouched**.

```
shared pure: Extractor.search · taxonomy · classify_url · resolve_deeplink · funnel_classify helpers
APP (frozen)                         FUNNEL (this work)
sweep.py → triage.py                 funnel_sweep.py → funnel_confirm.py
discovery.candidates (store req'd)   discovery.funnel_candidates (store_id NULLABLE; kind+status)
```

### Engine 1 — discover (`funnel_sweep.py`)
Sweeps the `funnel_phrase` taxonomy layer (KEYWORD_EXACT_PHRASE), **keeps web destinations** (inverse of the
app sweep's `non_app` drop), parses landing **domain + UTMs**, persists to `discovery.funnel_candidates`.
- Files: `src/agf/discovery/funnel_classify.py` (pure: `funnel_signal`, `landing_domain`, `parse_utms`,
  `matched_funnel_host`, host hints; reuses `agf.classify.unwrap_fb_shim`), `db/schema/120_funnel_candidates.sql`,
  `src/agf/db/funnel_candidates.py` (`upsert_funnel_candidate`, `new_funnel_candidates`, `cluster_by_domain`,
  `mark_funnel_confirmed`), `src/agf/discovery/funnel_sweep.py`, `scripts/run_funnel_sweep.py`.
- **Page-likes gate is funnel-specific:** `FUNNEL_INCUMBENT_PAGE_LIKES_MAX = 5_000_000` (in `scoring/vetoes.py`),
  NOT the apps-only `10_000`. Reason: proven funnel operators (Farmer's Dog, Spoiled Child) have huge FB pages
  *because they work* — the 10k veto was dropping 53% of funnel ads. Loosening took drops 79→2.
- **Domain clustering works:** the same funnel pushed by multiple persona/creator pages clusters by
  `landing_domain` (e.g. `mybody.health` ×3, `inito.com`/`spoiledchild.com` ×2). That's the dedup signal.

### Engine 2 v1 — confirm/label (`funnel_confirm.py`)
For each `new` candidate: render the landing page → fingerprint funnel tech → resolve the deeplink → write a
verdict (`mark_funnel_confirmed`).
- Files: reworked `src/agf/discovery/firehose_fingerprint.py` (`SIGNATURES`), `funnel_capture.render_landing_html`,
  `src/agf/discovery/funnel_confirm.py`, `scripts/run_funnel_confirm.py`.
- **`firehose_fingerprint.SIGNATURES` categories:** `checkout` (Solidgate `cdn.solidgate.com`/`solid-form.js`,
  Stripe, Primer, Paddle, Braintree, Recurly, PayPal, `fnlfx.com` — **NOT** app-side RevenueCat/Superwall/Adapty),
  `pixel` (fbq + GTM), `mmp` (Branch/AppsFlyer/Adjust/Singular), `store` (apps.apple/play.google).
- **`classify_funnel(html, resolved, funnel_host)` — order matters:**
  1. resolved→store (ios/android + store_id) → **install**
  2. `store`/`mmp` sig in HTML → **install**
  3. `checkout` sig → **subscribe**
  4. `pixel` sig OR funnel_host → **unknown** (kept; a paid funnel, payoff unclear)
  5. else → **dropped** (`no_funnel_tech` — generic web noise)
- Orchestrator `run_funnel_confirm(engine, *, resolve, render, ids, limit)` is **sync** and takes **injectable
  `resolve`/`render`** so the async Extractor + sync Playwright are wired only in the script (keeps it testable).

---

## CRITICAL gotchas (these cost time to rediscover)

1. **DB tests SKIP silently** unless `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf` is set. A "passing"
   run that says *skipped* tested nothing.
2. **Live runs need the sandbox OFF** (real network egress) and `ops/.env` sourced for `AGF_PROXIES`. Run via
   Bash with `dangerouslyDisableSandbox: true`, and source secrets inline (`set -a; . ops/.env; set +a`) so the
   proxy password never enters the transcript. Reading `ops/.env` directly (e.g. `grep`) is blocked — source it.
3. **`AGF_PROXIES` (DataImpulse residential) is for META egress ONLY.** Do **not** proxy the landing-page render
   — it times pages out. The render goes **DIRECT**.
4. **Landing render MUST use iOS UA + settle**, or the DOM is empty and everything mislabels. `render_landing_html`
   already does: iOS `RENDER_UA`, `wait_for_load_state("networkidle", 8s)`, 2.5s settle, CMP dismiss, then
   `content()`. Verified: `betterme.world` sigs `set()` → `[mmp,pixel,store]` only after this.
5. **Migration:** apply via `apply_migrations(get_engine())` (auto-applies `db/schema/*.sql` in order). Funnel
   table is `120`. (118/119 are reserved by the unmerged `feat/gemscore-traction-axis` branch — keep funnel ≥120.)
6. **Go API deploy** (if you touch it later): build → `mv ~/bin/agf-api` → `launchctl kickstart com.fielddesk.api`;
   apply matview migrations BEFORE the new API binary.

## Run commands

```bash
# tests (DB ones need the env var)
AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf .venv/bin/pytest \
  tests/discovery/test_funnel_classify.py tests/discovery/test_funnel_sweep.py \
  tests/discovery/test_funnel_confirm.py tests/discovery/test_firehose_fingerprint.py \
  tests/db/test_funnel_candidates.py tests/enrich_full/test_render_landing.py -q

# live discover (Engine 1) — sandbox OFF
set -a; . ops/.env; set +a
AGF_DATABASE_URL=postgresql://agf:agf@localhost:5433/agf AGF_FUNNEL_MAX_KW=8 AGF_SWEEP_PAGES=2 \
  PYTHONPATH=src .venv/bin/python scripts/run_funnel_sweep.py

# live confirm (Engine 2) — sandbox OFF; renders landing pages direct + iOS UA
set -a; . ops/.env; set +a
AGF_DATABASE_URL=postgresql://agf:agf@localhost:5433/agf AGF_CONFIRM_LIMIT=30 \
  PYTHONPATH=src .venv/bin/python scripts/run_funnel_confirm.py
```

Local stack: Postgres `:5433`, MinIO `:9010`, Go API `:8000`, web `:3000` — all currently up.

---

## Roadmap (user's chosen sequencing: surface → continuous)

1. **Surface funnels (NEXT).** Right now `discovery.funnel_candidates` is invisible — no scoring, no feed/API.
   Needed: a funnel score (the research §7 signals: localization/#languages, ad longevity, creative velocity,
   the `kind` label, pricing) + a way to see them (a funnel feed/API, or fold into the existing Discover UI).
   Until this exists, continuous accrual would fill a table nobody can see.
2. **Then wire continuous accrual** — a daily funnel sweep+confirm (worker stage or separate launchd/cron),
   so funnel velocity history accrues like the app pipeline. Only after surfacing.
3. **v2 confirm refinements:**
   - Answer-aware **quiz-stepping** to reach the post-quiz paywall → this is what unlocks `subscribe` labels
     (v1 renders the landing page only, so `subscribe`=0 and many real funnels land as `unknown`).
   - Recall gap: real funnels on apex domains with no landing-page tech + no funnel host get **dropped**
     (e.g. `inito.com`). Stepping or a softer drop rule would recover them.
   - Screenshots → S3 for a funnel detail view.
4. **Finish labeling the pool:** 27 candidates are still `status='new'` — re-run the confirm script.

## Adjacent threads (validated by the product eval, not funnel-specific)

- **Traction axis (GemScore):** specced on branch `feat/gemscore-traction-axis`, **NOT built**. The product eval
  confirmed it's needed — a 0-review app (FoodHealth) tops the Discover feed at #1. Spec:
  `docs/superpowers/specs/2026-06-11-gemscore-traction-axis-design.md`.
- **Discover feed issues** (memory `discover-product-eval-2026-06-12`): duplicate apps via multiple advertiser
  pages (needs advertiser↔app dedup); score≠longevity; Saved page loses icons + no delta-since-save; the
  "Real funnel only" filter was a no-op (294/299) — Engine 2's labels can now make a funnel filter meaningful.

## How to finish the branch

When ready: tests green → `superpowers:finishing-a-development-branch` (merge to main / PR / keep). The branch is
large and self-contained; nothing depends on it yet.
