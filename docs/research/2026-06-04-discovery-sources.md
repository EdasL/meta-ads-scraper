# Discovery Source Research — What To Use For Finding Emerging Apps & Funnels

**Date:** 2026-06-04 · **Status:** researched (9-agent workflow) + **PoC-validated on this machine** · **Feeds:** Plan 4 (Discovery firehoses)

> **TL;DR.** Discovery should be **spend-driven, not store-recency-driven.** The proven engine is the **Meta Ad Library (Firehose B/C)** — a direct *paid* signal — and the app-store "new apps" lane is a cheap **secondary seed + release-date post-filter**, not the engine. The free iOS new-apps feed (legacy iTunes RSS) **is live and genuinely fresh** (PoC: 100% of apps released within a day) but **volume-limited (~100–120 newest/run, no genre multiplication)** — poll it daily and accumulate. Android has no free "new" feed. Web funnels need their own lane (**PublicWWW** fingerprinting). No clean ≤$300/mo paid API does iOS+Android new-app discovery; the only worthwhile cheap *convenience layers* are **ScrapeCreators ($10/mo)** for the Meta side and **PublicWWW (~$49–79/mo)** for funnels. Total core stack = **$0**.

---

## 1. The strategic conclusion (adversarially verified: *supported*)

For "apps **scaling on paid Meta**," the Meta Ad Library spend signal beats any app-store "new/trending" chart, because:
- App-store charts blend organic + paid, are **top-N capped**, **incumbent-biased**, and **gameable** (burst campaigns spike install velocity into Top-10 then ride the organic halo). They surface "the biggest, not the emerging."
- The Ad Library is an **ad archive** → its impressions are *paid* impressions; ad **longevity + impression bucket** is a direct, keyword-free scaling proxy that surfaces unknown advertisers.

**So Firehose B (Meta Ad Library, EU impressions sort) stays the PRIMARY discovery engine** (already proven live in `2026-06-04-server-side-extraction.md`). The app-store lane's only jobs are (a) a cheap *extra* seed of brand-new apps the Meta radar hasn't caught yet, and (b) the **release-date post-filter** (the `emerging` gate). It does **not** need to be comprehensive or recency-ranked itself.

---

## 2. PoC — is the free iOS "new apps" feed real or frozen? (this machine, 2026-06-04)

The research surfaced a **direct conflict**: one agent's live test said the legacy iTunes RSS new-apps feed works; another cited Apple-forum reports of it being "frozen since ~2016." A PoC resolved it — fan the feed across 9 genres × 2 kinds, then check authoritative `releaseDate` via iTunes Lookup:

```
18 feed calls → all HTTP 200, 108 entries each
119 UNIQUE app ids
release-date age:  <=7d: 119 (100%)   <=30d/90d/older: 0
   newest: Palm Estates, JWT Lens, Padiconnect, Forq, Toon Tone … all released 2026-06-03 (0d ago)
negative control: modern v2 /apps/new-apps-we-love → HTTP 404 ;  /apps/top-free → 200
```

**Verdict: LIVE and genuinely fresh** (not frozen) — `https://itunes.apple.com/us/rss/{newapplications|newfreeapplications|newpaidapplications}/limit=100/genre={id}/json`, free, no auth. **Two corrections to the research, caught by the PoC:**
- **Genre fan-out does NOT multiply coverage** — 18 calls returned only 119 unique (~94% overlap); the new-apps feed returns ~the same ~108 *globally*-newest apps regardless of `genre`. Treat it as **one ~100–120-newest-apps list, polled daily** (→ thousands/month via dedup), not "26 genres → thousands per run."
- **It's a pure seed, not a signal** — every hit is a day-0 app with 0 ratings and ~no Meta spend yet. Useless alone; only valuable **cross-checked against the Ad Library** and aged-filtered.

The modern `rss.applemarketingtools.com` v2 generator is confirmed **useless for "new"** (the `new-apps-we-love` kind 404s; only top-free/top-paid resolve, capped at 100).

---

## 3. Source matrix — what to use

### Free core stack ($0)
| Source | Role | Reality |
|---|---|---|
| **Meta Ad Library** (our curl_cffi scraper, Plan 2) | **PRIMARY discovery** (Firehose B/C) | Proven; spend-ranked; the engine |
| **Legacy iTunes RSS new-apps** (`itunes.apple.com/{cc}/rss/newapplications…`) | iOS fresh-app **seed** | Live (PoC), ~100–120 newest/run, **poll daily**, undocumented → add a health check |
| **iTunes Lookup** (`/lookup?id=…,…`) | release-date **post-filter** | Free, batch ~200/call, ≤20/min. **Use `releaseDate`, NOT `currentVersionReleaseDate`** (the latter is the last *update* → mis-tags old apps as new) |
| **facundoolano/google-play-scraper** | Android release-date enrichment + similar/dev expansion | Active but scrape-fragile; **no "new" collection** (Google removed NEW/MOVERS) |
| **Google Play sitemap** (`play.google.com/sitemaps/sitemaps-index-0.xml`) | optional full Android enumeration | Free + comprehensive (~80k gzip files × 50k URLs) but **no recency/spend signal** → heavy crawl; last resort |
| **PublicWWW** (free top-1M) | web-funnel **fingerprint** seed | Source-code search for paywall-SDK ∩ Meta-Pixel ∩ MMP; free tier validates fingerprints |
| **AppGoblin/adscrawler** (`ddxv/adscrawler`) | open-source reference + free per-app lookups | 6M+ apps; mirror its `--new-apps-check` design |

### Paid convenience layers (add only when a gap is measured)
| Tool | Cost | Why |
|---|---|---|
| **ScrapeCreators** | **$10/mo** (100 free credits) | Meta Ad Library without app-review / 200-call-hr quota friction; reaches US commercial ads. ToS-gray (3rd-party), keep as a *convenience* layer, not sole source |
| **PublicWWW paid** | ~$49–79/mo | Full 200M-site index + CSV/API for the funnel lane at scale |
| **Appfigures Public Data API** | ~$350/mo (Grow) + credits | Only if iOS seed coverage proves insufficient — release_date + Top Charts for *any* app. Buy **last** |

### Skip (enterprise-priced, wrong budget)
Sensor Tower (acquired data.ai Mar 2024) ~$25–40k/yr · Apptopia ~$50–100k/yr · 42matters (now Similarweb) quote-only ~$650+/mo · AppMagic API (but its **free** web tools — *Top Soft Launches*, *Top Trending* — are useful manual seeders).

### Note on the official Meta `ads_archive` API
Confirmed **not usable** for US commercial discovery — it's political/issue-only globally + all-ads only for EU-delivered (DSA). That's *why* Firehose B is an EU-impressions play, and why our web-GraphQL scraper (Plan 2) is the real US path. (Matches the existing §2 north-star.)

---

## 4. Recommended discovery architecture (solo / local-now, hosted-later)

```
PRIMARY  ── Meta Ad Library (Plan 2 curl_cffi scraper)  ──┐  spend-ranked, keyword-free
           EU impressions firehose + EU→US bridge          │  (the engine; proven)
WEB      ── PublicWWW fingerprint (paywall SDK ∩ pixel ∩ MMP)┤→ candidate bucket → scaling-confirm
SEED     ── legacy iTunes RSS new-apps (poll daily, dedup) ──┤   (14d-active gate, Plan 4)
           [Android: seed from Meta + iOS→Android match]     │→ release-date post-filter (Lookup)
                                                             ┘   → GemScore (Plan 5)
```
- **$0** to run the core. Optional **$10/mo ScrapeCreators** + **~$49/mo PublicWWW** remove friction at volume.
- Every lane dumps candidates into one bucket; **the Ad Library scaling-confirm (≥14d active) is the gate**, and Lookup's `releaseDate` is the `emerging` filter — both applied *after* discovery, so no lane needs to be recency- or spend-complete on its own.

---

## 5. Implications for Plan 4 (Discovery firehoses)

- **Firehose A (rebuild):** drop the modern v2 generator + the "26-genre fan-out → thousands" assumption. Implement as a **daily-polled legacy-RSS accumulator** (~100–120 newest/run, deduped over time) + iTunes Lookup `releaseDate` post-filter. Add a **health check** (alert on non-200 or entry-count/recency collapse — the feed is undocumented and *has* frozen historically). MWM catalog demoted to optional enrichment (scrape-only, pivoting to an AI builder).
- **Android:** accept **no free "new" feed**; seed Android from Meta + iOS→Android publisher/title matching; `google-play-scraper.app()` for release dates; Play sitemap only if full enumeration is ever needed.
- **Firehose C (web funnels):** PublicWWW free tier for fingerprints; upgrade to paid only for exports/scale.
- **Optional convenience:** ScrapeCreators as a Meta-side discovery layer alongside our scraper.
- **Dedupe/identity:** bridge Meta `page_name`/destination-domain → canonical store id → release date; fuzzy publisher/title match for iOS↔Android. (Already partly covered by Plan 4 P4-T7 `link_app_to_advertiser`.)

---

## 6. Risks / caveats
- **Legacy RSS is undocumented** → health-check it; it can freeze without notice (forum history is real even though it's live today).
- **`releaseDate` vs `currentVersionReleaseDate`** — use the former for the emerging gate.
- **Scraper breakage** (google-play-scraper, app-store-scraper) — pin versions, monitor.
- **ToS** — ScrapeCreators/PublicWWW are 3rd-party scraping (defensible post-2024 *Meta v. Bright Data* for public data, but reselling-gray); Appfigures forbids re-publishing its data. Keep private-analysis posture.
- **Volume reality** — ~100–120 new iOS apps/run is modest; the real funnel is Meta-spend discovery, with the RSS as a thin freshness top-up.

## Sources (selected)
- PoC (this machine, 2026-06-04): legacy RSS live, 100% <7d-old; v2 new-apps 404.
- Apple: legacy `itunes.apple.com/rss/newapplications`, `/lookup`; `rss.applemarketingtools.com/api/v2`; Play `sitemaps-index-0.xml`.
- Tools: `facundoolano/google-play-scraper` + `app-store-scraper`, `digitalmethodsinitiative/itunes-app-scraper`, `ddxv/adscrawler`/AppGoblin, PublicWWW, BuiltWith.
- Paid: Appfigures Public Data API (Grow ~$350/mo), 42matters→Similarweb, Sensor Tower/data.ai (enterprise), AppMagic free tools, **ScrapeCreators ($10/mo)**.
- Verdicts: spend-driven > charts (*supported*); free comprehensive recent-apps (*mixed* — Android Play-sitemap exists, iOS doesn't); ≤$300/mo full iOS+Android new-app API (*mixed* — Appfigures gated to ~$350/mo).
