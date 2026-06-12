# Funnel-builder / paywall-vendor client lists as SEED SOURCES

Research date: 2026-06-12. Method: Firecrawl CLI (`scrape --only-main-content`, `map`)
against live vendor sites, indexed via context-mode. Goal: harvest real NAMED funnel
operators (web2app subscription apps) to seed Meta-ad + web-funnel tracking.

## Per-vendor verdict

| # | Vendor | Page scraped | Names brands? | ~Named count | Firecrawl-scrapable? | Seed yield |
|---|--------|--------------|---------------|--------------|----------------------|------------|
| 1 | Adapty | adapty.io/case-studies/ | YES (text + links) | ~30 | YES, clean | HIGH (best) |
| 2 | Purchasely | purchasely.com/customers + homepage logo wall | YES (text headings) | ~18 + 8 logo-wall | YES, clean | HIGH |
| 3 | Solidgate | solidgate.com/case-studies | YES (text) | ~6-8 | YES (Next.js, rendered) | MED |
| 4 | FunnelFox | blog.funnelfox.com/category/web2app-case-studies | YES (post titles) | ~7 | YES | MED |
| 5 | Heyflow | heyflow.com/customer-success-stories | YES (text) | ~12 | YES | MED (mostly B2B lead-gen, NOT web2app apps) |
| 6 | web2wave | web2wave.com (homepage) | NO real names | 0 | logos = images only (.avif); testimonials are STOCK names | NONE |
| 7 | Unbounce | /customers redirects to marketing-resources | Barely (1: Going.com) | ~1 public | YES but no list page | LOW |
| 8 | Funnelish | funnelish.com (no /customers) | NO | 0 | YES but page has no client names | NONE |

Notes:
- Adapty real customer URLs: each is `adapty.io/case-studies/<slug>/`.
- web2wave logo filenames seen (images-only, need OCR/alt): leya, homemadelog, neatsy,
  cacao, kids, moonly, prosto-green, union-apps — NOT reliably resolvable to brands.
- web2wave testimonials use fabricated stock names (James Rodriguez, Anna Peterson,
  Emily Chen) — discard.
- Heyflow clients are lead-gen / B2B (solar, mortgage, recruiting), NOT subscription apps
  — off-thesis for web2app funnel tracking. Rocket Mortgage appears as a logo.
- Unbounce: the old logo wall is gone; named cases live in scattered blog posts.
- Funnelish: homepage is all features/blog/FAQ; no testimonials-with-names, no /customers.

## Consolidated deduped brand list (real funnel operators / sub apps)

### Tier 1 — subscription apps (on-thesis web2app/paywall operators)
Adapty: Glam AI, AI Video, Welmi, Pregnancy+, AppDevLabs, Feeld, 44pixels, BibleChat,
Letterly, SocialKit, Avatarify, Shmoody, Lively, GlowUp, Pepapp, Wave, Impala,
Moonly, Prosto, Secret/AI Photo Generator, Text on Pic, Productivity App.

Purchasely: TomTom, Wattpad, Hatch, Sleep Cycle, Le Monde, Salams, Reverso, Ornikar,
Happn, Kidly, TextMe, Hornet, Animation Digital Network (ADN), Lake, TorAlarm,
Muslim Pro, FlipaClip, Busuu, Headspace, The Times, PhotoRoom, BBC, immowelt, NZZ.

FunnelFox: Reading.com, Talaboos, Prehab, ExploreApps, Glam AI, English on Smartphone.

Solidgate: MEGOGO, HOLYWATER (Holywater Tech), Zeely, Ajax Systems, Higgsfield, DressX.

### Cross-vendor overlaps (strong signal — appear at 2+ vendors)
- Glam AI → Adapty + FunnelFox
- Muslim Pro → Purchasely (+ Purchasely "trending topics")
- TomTom, Busuu, PhotoRoom, The Times → Purchasely

### Tier 2 — off-thesis (lead-gen / non-sub, lower priority)
Heyflow: Vertigo Media, 1KOMMA5°, F Street, Atomic Marketing, Airfocus, BOLT DMS,
Jacobs Coffee, Leantree, Windmill Microlending, Book More Showings, helpcheck,
Dan Carpenter; Rocket Mortgage (logo).
Unbounce: Going.com.

## Vendor ranking by harvestable named funnel operators
1. Adapty (~22 sub apps, clean text, per-app URLs) — best single seed.
2. Purchasely (~24 named, incl. logo wall) — best for established sub-app brands.
3. Solidgate (~6, all real sub/app operators, high quality) — small but on-thesis.
4. FunnelFox (~6, directly web2app) — small but exactly the niche.
5. Heyflow (~12 but B2B lead-gen, off-thesis).
6. Unbounce (~1 public).
7/8. web2wave & Funnelish — zero harvestable real names.

## Recommendation
Automate Adapty + Purchasely + Solidgate + FunnelFox case-study/customer pages as a
recurring seed feed (~50 unique real sub-app operators, dedupe on overlaps). All four are
clean-text Firecrawl scrapes — no OCR needed. Skip web2wave (images-only) and Funnelish
(no names). Treat Heyflow/Unbounce as opportunistic, not core.
