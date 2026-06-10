# Read-before-you-like + board = liked

**Date:** 2026-06-05
**Status:** approved (founder)

## Problem
The board shows every candidate gem; "the pile" is a 3-way yes/maybe/no stamp;
the dossier shows only raw signals. A user can't easily understand *how an idea
works* or *why we flagged it* before committing to it.

## Goal
Let users read a plain-language explanation of an idea (how it works + why it's
good) before liking it. Board becomes the collection of liked ideas. Discovery
is where you triage candidates.

## Flow
1. **Discover** (renamed from "the pile"): feed of candidate gems not yet liked
   or passed.
2. Open an idea → **dossier** with two new top sections: **how it works** and
   **why it's a good idea**.
3. Two actions everywhere a candidate appears: **like** (save to board) and
   **pass** (dismiss from discover). The old 3-way stamp ("maybe") is dropped.
4. **Board = liked only.** Draggable canvas of liked ideas; empty state nudges
   to discover. Unlike from a board card returns it to discover.

## Data / persistence
- `lib/likes.ts`: localStorage-backed like/pass store keyed by `advertiser_id`,
  source of truth in both mock and live mode (no new backend contract needed for
  the board flow). State per id: `"liked" | "passed" | null`. Emits a change
  event so board/discover/dossier stay in sync.
- Live mode also POSTs the existing review decision (like→accept, pass→reject)
  as a side effect so the server records it, but UI state derives from the local
  store.

## "Why it's a good idea" — derived, no new fields
`lib/reasons.ts` `buildReasons(gem)` → ordered reason list, each with the number
behind it, from existing scoring data:
- `scaling_velocity` → "grow fast — ads go X → Y in N week"
- `under_radar` → "still hidden — only Nk follower / small reach"
- `funnel_intent` → "real money — paywall by step N, paid trial"
- `max_longevity_days` → "not fluke — run N day straight"
- `alert` → "getting big now. move quick."

## "How it works" — needs content
- Add optional `how_it_works?: string` to `GemDossier`.
- Mock fixtures: write a 2-3 sentence plain explanation per gem (grug voice).
- Fallback (`reasons.ts` `howItWorks(gem)`): synthesize from `tagline` + funnel
  steps when the field is absent (covers live data until enrichment fills it).
- Live note (out of scope now): enrichment must eventually populate this field.

## Touched files
- `lib/likes.ts` (new), `lib/reasons.ts` (new)
- `types/contracts.ts` (+`how_it_works?`)
- `lib/mock/fixtures.ts` (+content)
- `components/dossier/Dossier.tsx` (sections + action bar)
- `components/board/Board.tsx` (liked-only + empty state + unlike)
- `components/review/Review.tsx` (→ discover, like/pass)
- `components/AppHeader.tsx`, `components/landing/Landing.tsx` (rename)

## Out of scope
- Backend `how_it_works` enrichment; server-side likes read endpoint.
