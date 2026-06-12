# Funnel discovery — finding web quiz funnels that drive app installs & plan purchases

**Date:** 2026-06-11 · **Goal:** a concrete, runnable method to put *web quiz/sales
funnels* — the "take a 60-second quiz → get your personalized plan → install the app
/ start the subscription" cohort — into the tracked pool. Today the pipeline is
**apps-only**; funnels are detected by the research harness but never reach production.

This is an **assembly problem, not a greenfield one**: the keyword layer, the host
heuristics, the tech fingerprint, and MMP redirect-resolution all already exist. The
method below stands up a **separate funnel pipeline** that *reuses the pure primitives*
but never modifies the working apps-only flow.

**Architectural decision (2026-06-11, user):** build a sibling funnel pipeline, do NOT
retrofit funnels into the app pipeline. Reason — the app pipeline is **store-mandatory**:
`discovery.candidates` requires `store`+`store_id` (`sweep.py:53-59`) and `triage.py`
keeps/drops on store metadata. Funnels are **store-optional** (a quiz→subscribe funnel may
have no app). Forcing them into one flow means nullable columns, branchy triage, and a
real regression risk to the working path. Fork the *pipeline*; share only *pure
primitives*. See §6 for the exact seam.

---

## 1. The target fingerprint (what we are hunting)

A **web2app / web2subscribe quiz funnel** is a Meta advertiser whose ad:

- carries **quiz/funnel intent** — `FUNNEL_PHRASES` copy ("take the quiz", "personalized
  plan", "based on your answers"…) or a lead-gen CTA (`SIGN_UP`/`SUBSCRIBE`/`LEARN_MORE`);
- points to a **web landing page** — `classify_url(link_url).kind == "web"` (NOT a direct
  store link), on a **funnel host** (`quiz.`/`try.`/`start.`/`typeform`/`involve.me`/
  `adapty`/`reverse.health`…), and NOT a social host; and
- whose landing page, once resolved, is one of two payoff types:
  - **quiz→install** — the funnel deep-links to an app store via an MMP
    (`branch`/`onelink.to`/`adjust`) → it drives **app installs**;
  - **quiz→subscribe** — the page runs a **paywall SDK ∩ Meta Pixel** (RevenueCat /
    Superwall / Adapty + `fbq(`) and a checkout/price → it sells **a plan** (often a
    web2app sub that *also* has an app).

This fingerprint is near-unique to the emerging subscription-app cohort, which is exactly
why it has the **lowest incumbent contamination** of any discovery seed.

## 2. Why production finds zero funnels today (the gap, with refs)

- `discovery/sweep.py:51` — `if intent.kind not in ("ios","android"): return "non_app"`.
  Every web/funnel destination is dropped before persistence.
- `discovery/taxonomy.py:188` — `sweep_entries()` returns `niche_term + store_fragment`
  only; the `funnel_phrase` layer (40 quiz phrases) is deliberately left out ("v2").
- `discovery/triage.py` + `discovery.candidates` — the candidate row **requires**
  `store`+`store_id` (`sweep.py:53-59`), so a funnel (no store id) cannot be stored.

So the funnel cohort is structurally excluded at three points. Nothing about egress,
scoring, or velocity is the blocker — only candidate admission.

## 3. The funnel pipeline — four stages

All four stages live in a **new sibling pipeline** (`discovery/funnel_sweep.py` →
`discovery/funnel_confirm.py`), invoked by the worker alongside — never inside — the app
sweep. The app pipeline (`sweep.py` → `triage.py`) is not touched.

### Stage 1 · Discover (get funnel candidates into the funnel pool)

Runs in `funnel_sweep.py`. Discovery is a **seed-and-expand** problem (the same loop the
paid tools run — Adheart/Tyver/SocialPeta — see §8). Three engines feed each other; dedup
by `page_id`. The first finds funnels on Meta, the second finds funnels *not* on Meta, the
third bootstraps the seed set.

- **Engine 1 — Meta page-walk + destination/UTM clustering (biggest lever, fully ours).**
  Cold-start with the funnel-phrase exact-match sweep
  (`taxonomy.all_entries("funnel_phrase")`, `KEYWORD_EXACT_PHRASE` — already built, yielded
  491 funnels in a prior sweep). Then **expand**: for every seed advertiser, harvest its
  ads' `link_url` + `caption` + **UTMs** (already on the snapshot), normalize to a landing
  **domain**, and **cluster advertisers by shared domain / UTM / pixel-id** — siblings
  running the same offer fall out. (The Ad Library keyword box matches *ad copy only* — you
  cannot query it by destination domain — so the domain clustering happens on our side after
  harvesting `link_url`s. Adyntel/Adheart productize exactly this.) This expansion, not the
  cold-start keywords, is where the volume comes from.
- **Engine 2 — Code-fingerprint expansion (PublicWWW/NerdyData; finds funnels NOT on Meta).**
  Scrape one funnel (Firecrawl) → extract its **homepage-present** fingerprints → reverse-
  lookup every other site sharing them. **Empirically corrected (live homepage check of
  BetterMe / Simple / Reverse Health / Cal AI):** the Solidgate `solid-form.js` / Adapty
  `*.fnlfx.com` **checkout SDK is injected only at the post-quiz payment step and is ABSENT
  from the homepage**, so static crawlers (PublicWWW, HTTP Archive's root+1-page crawl) never
  index it — you **cannot enumerate funnels by the checkout signature**. Key instead on the
  homepage layer that IS indexed: **MMP (`onelink.me`, `adj.st`/`adjust.com`, `app.link`) +
  Meta Pixel + GTM** — high recall, low precision, so **AND it with a vertical keyword**.
  Tooling: PublicWWW (519M pages, exact source match, free top-1M / ~$49·mo — the workhorse,
  parser already in `firehose_fingerprint`); NerdyData (renders JS → catches GTM-injected
  scripts PublicWWW misses); HTTP Archive on BigQuery (`httparchive.crawl.requests`,
  `url LIKE '%onelink.me%'`, ~free, homepage-only supplement).
- **Engine 3 — Seed lists (bootstrap the known-funnel set; named + scrapable).**
  Resolve each named brand → Meta page + live funnel, then feed Engines 1–2. Sources
  (all verified live 2026-06-12): **Apple top-grossing RSS** (`genre=6013` Health&Fitness /
  `6012` Lifestyle — complements our existing iTunes *new-releases* seed), **Screens Design**
  (2,538 named apps via `firecrawl map`), **PaywallScreens** (10,229 named paywalls),
  **Page Flows** (393 brands), and vendor case studies (Adapty/Purchasely/Solidgate/
  RevenueCat, ~50 operators) + the Adapty Paywall Newsletter feed. ~120 named brands already
  harvested → `docs/research/app-store-reverse-seed-2026-06-12.md` +
  `docs/research/funnel-vendor-seed-lists-2026-06-12.md`.
- **Per-brand subdomain enumeration (cheap expansion).** crt.sh CT-log query
  (`?q=%25.<domain>&output=json`) returns an operator's funnel hosts — **verified: 287
  hostnames for betterme.world incl. `api-quiz.`, `automation.quiz.`, `app.`**. Scrape those
  to reach the live quiz funnel. (MMP subdomains like `<brand>.onelink.me` live on the MMP's
  wildcard cert → get those from the scraped funnel HTML, not CT.)

> **Discover vs. confirm — keep the signatures straight.** DISCOVER on homepage-present
> signals (MMP + pixel + funnel-phrase + funnel host). CONFIRM the subscribe payoff on the
> post-quiz render (Solidgate / `fnlfx` / Stripe — Stage 2). The checkout SDK is a *confirm*
> signal, never a *discovery* one.

### Stage 2 · Confirm + classify (the discriminating step — mostly built)

For each web candidate, resolve and bucket — this is what separates a real
quiz→install/subscribe funnel from a generic web brand:

1. **App-install signal (static, robust — 6/6 in the live test).** Grep the landing HTML
   for an **MMP smart-banner / store link**: `adjust`/`appsflyer`/`branch`/`onelink` or a
   direct `apps.apple.com`/`play.google.com` link. Present → **quiz→install**; capture the
   `store_id` straight from the HTML (often no redirect-follow needed). `resolve_deeplink`
   (iOS UA) is the fallback when only an MMP wrapper is present.
2. **Subscribe signal — must be RENDERED, consent-accepted, and POST-QUIZ.** The live test
   showed the Meta pixel + checkout load via GTM behind a CMP (static search misses them),
   and the real paywall PSP sits on the **post-quiz checkout screen, not the homepage** —
   my homepage scrape saw Stripe/Primer but not the niche's actual tells. So step *through*
   the quiz with **`enrich_full/funnel_capture.py` (Playwright, auto-dismisses CMPs)**, then
   fingerprint the paywall. **Highest-signal PSP/paywall tells (research 2026-06-12):**
   **Solidgate** (`cdn.solidgate.com/js/solid-form.js` — the web2app niche's dominant PSP,
   BetterMe's), and **`*.fnlfx.com`** Adapty/FunnelFox-hosted paywalls (already in the repo's
   `SIGNATURES["paywall"]` — keep it). Then **Stripe** (`js.stripe.com/v3`), Primer, Paddle,
   PayPal, Braintree, Recurly. Quad confirm: `(Solidgate|Stripe) + (fnlfx|Next.js) + MMP +
   pixel` = near-definitive web2app subscription funnel.
3. **Quiz-step confirmation** *(light new work)* — `funnel_capture` already screenshots up
   to 3 steps; add a check that ≥2 question/answer steps precede the paywall (real quiz vs.
   one-screen landing). Neither install nor subscribe signal, no quiz steps → **drop**.

> **Code change implied:** `firehose_fingerprint.SIGNATURES` must be reworked — its
> `paywall` list (RevenueCat/Superwall/Adapty) is app-side and matched 0/6 live; replace
> with web-checkout providers, and move pixel confirmation out of static search into the
> Playwright render pass.

### Stage 3 · Persist + link (in the funnel pipeline's own store)

- **Own candidate table.** `discovery.funnel_candidates` — `page_id`, `page_name`,
  `page_like_count`, `landing_url`, `funnel_host`, `cta_type`, `source_keyword`,
  `ad_start_date`, `kind` (`install|subscribe|unknown`), nullable `store`/`store_id`,
  `status`. The app pipeline's `discovery.candidates` is left exactly as-is.
- **Own persist call** (`db/funnel_candidates.py`), parallel to `db/candidates.py` — no
  shared mutable schema, no store_id requirement.
- **Link funnel → app.** Add `funnels.app_id` FK (+ resolved `store_id`/`store`). When
  Stage-2 lands a store id, link the funnel to the app it drives — the literal "funnels
  that lead to people installing the apps". Quiz→subscribe funnels with no app keep the
  plan/price (already captured) as their artifact.

### Stage 4 · Validate live (before productionizing)

Run the funnel-phrase sweep (US, shallow) → web candidates → resolve each → report the
split: **% quiz→install** (store link/MMP) vs **% quiz→subscribe** (rendered checkout+pixel)
vs **% dropped**. That precision number is the go/no-go for the daily funnel run.

## 4. Live validation (2026-06-11)

Scraped raw HTML of 6 known emerging web2app quiz funnels and ran the repo's **actual**
`firehose_fingerprint` classifier + a broadened probe:

| funnel | repo `paid?` | Meta pixel (static) | repo paywall hit | real web checkout | MMP / store link |
|---|---|---|---|---|---|
| betterme.world | ✗ | none | none | **Stripe** | onelink, adjust, +store |
| fastic.com | ✗ | none | none | — | appsflyer, adjust |
| reverse.health | ✗ | none | none | **Primer** | adjust, +store |
| sensa.health | ✗ | none | none | **Stripe, PayPal** | adjust, +store |
| simple.life | ✗ | none | none | **Primer** | — |
| zing.coach | ✗ | none | none | — | onelink, adjust, +store |

**Three findings that reshaped the method:**
1. **The repo `paywall` list (RevenueCat/Superwall/Adapty) matched 0/6** — those are
   *app-side* SDKs. Real web checkout is **Stripe / Primer / PayPal**. → broaden signatures.
2. **The Meta pixel was in 0/6 static HTML** — it loads via **GTM** (4/6) behind a **CMP**
   (Cookiebot/OneTrust, 3/6). → static `paywall ∩ pixel` (PublicWWW or raw-HTML grep) is
   unreliable; pixel/checkout confirmation must happen in a **rendered + consent-accepted**
   pass (`funnel_capture` Playwright, which already dismisses CMPs).
3. **The app-install signal is static and robust** — an **MMP smart-banner or store link
   sits in the landing HTML for 6/6** (4/6 have a direct App Store / Play link). → the
   "quiz→install" bucket is cheaply detectable without rendering.

Reproduce: `/tmp/funnel-val/*.html` (scraped) + the probe script in this session.

## 5. Open decisions (for the user)

- **Which seed leads** — funnel-phrase sweep (A, fastest, English-biased) vs. add the
  corrected tech fingerprint (B) for language-agnostic reach. Recommendation: A now, B next.
- **Subscribe-only funnels (no app)** — first-class gems (plan/price as artifact) or only
  keep funnels that resolve to an app install? Goal text says *both*, so default: keep both,
  labeled by `kind`.

## 6. Architecture — two sibling pipelines (the seam)

```
                 ┌─ shared pure primitives ─┐
 Extractor.search · taxonomy · classify_url · resolve_deeplink · page_like veto
                 └──────────┬───────────────┘
        ┌───────────────────┴────────────────────┐
   APP PIPELINE (untouched)              FUNNEL PIPELINE (new)
   sweep.py  → triage.py                 funnel_sweep.py → funnel_confirm.py
   discovery.candidates                  discovery.funnel_candidates
   (store_id REQUIRED)                   (store_id OPTIONAL; kind=install|subscribe)
                                         + funnel_classify.py (promoted from the harness)
                                         + firehose_fingerprint (corrected signatures)
                                         + funnel_capture (render/CMP/price)
                                         + funnels.app_id linkage
```

**Shared (reuse, never fork — all pure/stateless):** `Extractor.search()` egress
(proxy/pacing/backoff), `taxonomy` (each pipeline picks its own layers), `classify_url`,
`resolve_deeplink`, the `INCUMBENT_PAGE_LIKES_MAX` veto constant.

**New, funnel-only (zero edits to the app flow):**
- `discovery/funnel_classify.py` — promote `funnel_signal`, `FUNNEL_HOST_HINTS`,
  `FUNNEL_WORDS`, `LEADGEN_CTAS` out of `scripts/discover_experiment.py` into a real module.
- `discovery/funnel_sweep.py` — sibling sweep that **keeps** web destinations (the inverse
  of `sweep.py:51`'s `non_app` drop) and writes `funnel_candidates`.
- `discovery/funnel_confirm.py` — render + classify `install|subscribe|drop`, resolve store.
- `db/funnel_candidates.py` + `discovery.funnel_candidates` table; `funnels.app_id` FK.
- Rework `firehose_fingerprint.SIGNATURES` (web-checkout providers; pixel moved to render).
- A ≥2-step quiz check in `funnel_capture`.
- Worker calls both pipelines; they never import each other's stateful modules.

This keeps the working apps-only path frozen while the funnel path evolves independently —
the only shared code is pure functions that can't carry one pipeline's assumptions into the
other.

## 7. How practitioners find *working* funnels — and what we automate (research 2026-06-12)

Three web-research sweeps (ad-spy tooling, media-buyer doctrine, the web2app subscription
ecosystem) converged on one playbook. The richest source is **FunnelFox/Adapty** (founded by
an ex-Palta growth lead) — esp. its *75,000-BetterMe-ads* and *311-web-funnels* teardowns —
plus **RevenueCat "State of Subscription Apps."** Their published method **is the recipe we
are building**:

> Meta Ad Library (by advertiser) → **dedupe creatives** → parse **landing URLs + UTMs** →
> **render** each landing/paywall → **fingerprint the stack** → score on
> *active-ad count × longevity × #languages × creatives/week × PSP-present*.
> (Paid tools that sell exactly this: Adheart, Tyver, SocialPeta, AppMagic, FunnelFox.)

### The winner signals, ranked by practitioner trust → our detection

| # | Signal (why it's trusted) | Quantified rule cited | Our detection | Status |
|---|---|---|---|---|
| 1 | **Localization across geos/languages** — operators translate *only already-proven* funnels (English ≈60% of volume; each new language +3–5% rev) | running in **15+ geos** = scaled winner | count distinct ad `languages`/`countries` per advertiser | **NEW** — highest-value add; we already pull country per SERP, just not aggregated |
| 2 | **Ad longevity on a DR offer** — you don't keep paying for a loser | 30d strong · **60–90d certified** | `max_longevity_days` (already an SV + Traction input) | HAVE — keep ordinal/percentile, never a cutoff |
| 3 | **Sustained re-investment / creative velocity** | **10+ new creatives/wk**; start-dates spread over months | `iteration_rate` / `weekly_launch_counts` | HAVE |
| 4 | **Variant volume → one funnel** | **50+ active ads ≈ $25k/mo**; 100+ ≈ five-figure; **100+ creatives/mo = "serious" cohort** | `active_ad_count` + **creative dedup** | PARTIAL — need simhash on body_text + image-hash (one creative cloned across geos fakes "50 tests") |
| 5 | **Funnel tech stack** (the quad) | `(Solidgate\|Stripe)+(fnlfx\|Next.js)+MMP+pixel` | `funnel_confirm` rendered fingerprint | **NEW** (corrected signatures, §Stage 2) |
| 6 | **UTM naming convention** — `utm_campaign`/`utm_content` encode funnel-type, locale, bid, concept | — | parse the landing URL query string | **NEW** — cheap, rich, no extra request |
| 7 | **Polished, A/B-tested paywall** — intro offer (50–70% off first period) replacing trials; 3-option paywall, annual pre-selected & anchored vs monthly; exit-intent downsell; long personalized quiz (Noom = 113 screens) | — | `funnel_capture` price + step capture | PARTIAL — add paywall-option + exit-intent capture |
| 8 | **Dedicated funnel subdomain** (`quiz.`/`go.`/`try.`/`start.`) | — | `FUNNEL_HOST_HINTS` | HAVE |

### What practitioners warn AGAINST (false positives to guard)

- **Long runtime ≠ winner for non-DR / brand-awareness ads, or big brands** that can run
  unprofitable ads → our incumbent vetoes (`page_like_count`, `established_traction`) already
  cut the big-brand case; additionally gate on a DR/lead-gen CTA (we have `cta_type`).
- **Cost-cap/bid-cap ads** get left running by neglect → age can measure neglect, not success.
- **Retargeting / low-spend tests** linger and look long-running.
- Mitigation everyone repeats: **corroborate across 5–10 advertisers** and weight
  **replication + localization over raw age**. → our cohort-percentile normalization already
  scores a funnel *relative to peers*, not on absolute age.

### Net new work this research justifies (beyond §3)

1. **Language/geo aggregation per advertiser** (signal #1) — the highest-leverage add; pure
   aggregation over data the sweep already sees.
2. **UTM parsing** (signal #6) — extract `utm_*` from the landing URL into the funnel row.
3. **Creative dedup (simhash)** before any variant-count signal (signal #4).
4. **Solidgate + the corrected quad** in `firehose_fingerprint` (signal #5).

### Tech-stack fingerprint reference (for `funnel_confirm`)

| Layer | Grep for | Note |
|---|---|---|
| Payment | `cdn.solidgate.com/js/solid-form.js`, `js.stripe.com/v3`, `primer.io`, `cdn.paddle.com`, `braintreegateway`, `recurly`, `paypalobjects` | **Solidgate = the niche tell**; on the post-quiz paywall, not the homepage |
| Paywall host | `*.fnlfx.com` (Adapty/FunnelFox), `web2wave`, `heyflow`, `funnelish`, `/_next/` (custom) | `fnlfx.com` already in repo SIGNATURES |
| MMP (web→app) | `app.link`/`branch.io`, `onelink.me` (AppsFlyer), `app.adjust.com`/`go.link`, `sng.link` (Singular) | the install-handoff; static-detectable (6/6 in live test) |
| Pixel | `connect.facebook.net`/`fbevents.js`/`fbq(` — usually via `googletagmanager.com/gtm.js` | consent-gated → render+consent |
| Consent | `cookiebot`, `cdn.cookielaw.org` (OneTrust), `usercentrics` | dismiss to load pixel/PSP |

**Sources:** FunnelFox BetterMe-75k & 311-funnels teardowns; RevenueCat State of Subscription
Apps + web2app guide; Adapty web-paywall docs (`*.fnlfx.com`); Solidgate payment-form docs;
askneedle / adlibrary.com / VibeMyAd (longevity doctrine + false positives); Adligator spend
proxy. Scraped markdown under `.firecrawl/`. *Every numeric threshold is practitioner folklore
or vendor data — features, not constants.*
