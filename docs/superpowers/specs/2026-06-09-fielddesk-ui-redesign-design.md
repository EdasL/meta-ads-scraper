# Field Desk — UI/UX Redesign Spec

**Date:** 2026-06-09
**Status:** Approved direction; ready for implementation plan
**Research brief:** `docs/superpowers/research/2026-06-09-fielddesk-ux-research-brief.md`
**Scope:** Frontend only (`web/`). No backend/API contract changes in this pass.

---

## 1. Users & problem

**Primary user — the indie founder / builder.** Solo or tiny team hunting for a product to build
that *already has demand*. Time-poor, not an analyst, allergic to hype. Wants proof, not vibes.

**Problem:** *"I want to build a product that already works — but I can't tell what's genuinely
taking off early vs. noise or already saturated. By the time a winner is obvious, it's too late.
And I won't manually trawl ad libraries every day."*

**Field Desk's job, three beats:** **Discover** what's emerging now → understand **why** it's
emerging → **save + annotate** the good ones to act on. Everything else is noise to cut.

Secondary users (kept in mind, not optimized for): growth marketers (want creatives/angles),
investors/scouts (want earliest signal). The evidence-first detail screen serves them incidentally.

---

## 2. Goals & non-goals

**Goals**
- Make the app **scannable, readable, and guided** — the user is led, not left to figure it out.
- Surface **why** each gem is emerging in plain language, with evidence one click away.
- Frictionless **save + annotate** with an auto-captured "why it was emerging" snapshot.
- Tokenize the visual system (type/space/color) so the brutalist skin is *rigorous*, not ad-hoc.
- Remove decorative chrome that adds noise without helping discovery.

**Non-goals (this pass)**
- No new accent color — stay **strictly monochrome** (only soften pure `#000/#FFF`).
- No backend/API changes — save/annotate persists **client-side (localStorage)** for now.
- No new data pipeline work. Use existing endpoints + the SV/UR/FI components already returned.
- Not redesigning auth/login beyond restyling to the new tokens.

---

## 3. Information architecture

Two destinations + detail. Nav: **`discover · saved(n) · leave`**.

| Route | Was | Now |
|---|---|---|
| `/` | elaborate marketing Landing | signed-out: thin hero + "go in"; **authed → redirect `/discover`** |
| `/discover` | `/board` (drag canvas) | ranked bento feed (**old `/board` → redirect**) |
| `/gems/[advertiserId]` | dense dossier | evidence-first, 3-altitude detail |
| `/saved` | `/review` (accept/reject queue) | My Gems: saved list + kanban + compare (**old `/review` → redirect**) |
| `/login` | login | login, restyled |

The accept/reject/hold **Review verdict is folded into Saved** as an optional per-gem field; the
standalone review queue is removed.

---

## 4. Design tokens

Implemented in `web/src/app/globals.css` (Tailwind v4 `@theme inline` + CSS vars) and
`web/src/components/sketch/palette.ts`. **Hierarchy via weight + gray-shade only — never hue.**

### 4.1 Color (monochrome ramp)
- `--bg` (canvas) `#0D0D0F` · `--ink` (text) `#EDEDED` · `--ink-soft` `#D4D4D4` · `--muted` `#A3A3A3`.
- Neutral ramp tokens: `#0A0A0A / #171717 / #262626 / #404040 / #525252 / #737373 / #A3A3A3 / #D4D4D4 / #E5E5E5 / #F7F7F7`.
- Surface ladder (lighter = higher, 1px borders, **no shadows**): `--surface-0` canvas → `--surface-1` `#171717` → `--surface-2` `#1f1f1f` (card) → `--surface-3` `#262626` (hover/expanded) → `--surface-4` `#2e2e2e` (drawer/popover). Card border `--line` `#262626`; strong border `#404040`.
- Stamp/alert "red" stays **white/ink** (monochrome) as today — emphasis by inversion, not hue.
- **Acceptance:** every text/border token ≥ its WCAG target against the surface it sits on
  (`--muted` on `--surface-2` ≥ 4.5:1). Verified with a contrast check before sign-off.

### 4.2 Typography
Fonts unchanged (Geist / Geist Mono / Roboto Mono). New scale as CSS classes/vars:

| token | px | line-height | tracking | use |
|---|---|---|---|---|
| `--fs-xs` | 12 | 16px | +0.02em | grug labels, signal captions, meta |
| `--fs-sm` | 13 | 18px | 0 | secondary rows, sublines |
| `--fs-base` | 14 | 21px | -0.006em | body / UI / primary rows |
| `--fs-md` | 16 | 24px | -0.01em | prose / note editor only |
| `--fs-lg` | 20 | 26px | -0.02em | card titles, gem names |
| `--fs-xl` | 24 | 29px | -0.03em | GemScore on card |
| `--fs-2xl` | 32 | 36px | -0.04em | GemScore on detail, section heads |
| `--fs-3xl` | 48 | 50px | -0.06em | editorial display |
| `--fs-4xl` | 64 | 64px | -0.06em | hero only |

Rules: data views 12–20px; 32+ rationed. UI weight 510, headings ~590. Metrics
`font-variant-numeric: tabular-nums`, right-aligned. Prose measure capped ~34em (45–75ch).

### 4.3 Spacing & shape
- Scale: `--sp-0 2 · --sp-1 4 · --sp-2 8 · --sp-3 12 · --sp-4 16 · --sp-6 24 · --sp-8 32 · --sp-10 40 · --sp-12 48 · --sp-14 56` (px). Dense interiors use 4/8/12; 24/32 between sections.
- Radius 2–4px (`--radius-xs` 2px, `--radius-sm` 4px). No pills.

---

## 5. Screen specs

### 5.1 Discover (`/discover`) — `DiscoverFeed`
Replaces `Board.tsx`. Ranked **bento card grid**, responsive (1 col mobile → 2 → 3).

- **Header:** display heading `GEMS RISING THIS WEEK`; subline `grug find {n} today. top one first.`;
  `sort` select (gemscore ▾ / fastest rising / newest) — default **gemscore desc**; `more filter ⌄`.
- **Filter rail** (collapsed by default, ≤6 axes): GemScore band, scaling velocity, under-radar
  threshold, has-funnel, category (cohort), ad-age (`ads now > N day`). Default view = *rising this week*.
- **Card (`GemCard`) anatomy:** ScoreBadge (`--fs-xl`, top-left) + trend arrow/% + status chip
  (`new spark / hot now / cooling / steady`); gem name (`--fs-lg`) · cohort; mini sparkline OR
  trend line; one-line grug WhyLine; 3 `SignalChips` (filled/hollow by strength); `first seen N day ago`;
  `[watch]` SaveToggle (dominant on hover/focus). Whole card click → detail.
- **States:** loading `grug get board…`; **seed/empty** never blank — show top seeded gems +
  banner `these gem grug found today. look around.` (synthetic data labeled honestly); **no-results**
  = 3-element EmptyState `nothing match. loosen filter.` `[put back]`; error = existing Fallback restyled.
- **Computed client-side** from API SV/UR/FI: WhyLine (dominant signal), chip fill strength, status
  chip (from velocity + freshness), trend %. No backend change.
- **Keyboard:** `j/k` move focus, `S` watch focused card, `Enter` open. (Progressive; non-blocking.)

### 5.2 Gem detail (`/gems/[advertiserId]`) — split `Dossier`
Three altitudes, evidence-first. Split into `GemVerdict`, `EvidenceReel`, `ScalingTrend`, `GemFacts`.

- **Altitude 1 — verdict (always):** ScoreBadge `--fs-2xl` `87/100` + band word (`weak/maybe/strong/rare gem`)
  + trend arrow/% + status chip; one-sentence grug **Because** line naming the dominant signal; `how grug know? ⌄`.
- **Altitude 2 — push/pull (on expand):** two columns `WHY IT CLIMB` / `WHAT HOLD IT BACK`. Strong
  signal = filled/heavy ink; weak = hollow/Rough.js hatching. Per-signal renderers:
  - Scaling Velocity → Rough.js sparkline + arrow + % (a curve, never a lone number).
  - Under Radar → bar vs baseline: `only 3 run this · cat avg 40`.
  - Funnel Intent → checklist chips `landing ✓ · checkout ✓ · price ✓ · upsell ✓`.
- **Altitude 3 — proof (`see proof`):** EvidenceReel — actual ad creatives (Polaroid), 30-day
  active-ads sparkline (ScalingTrend, from `/gems/{id}/scaling`), funnel steps; each funnel ✓ links
  to the exact screenshot. Images lazy-load; 404s hidden (current behavior kept).
- **Facts:** GemFacts row — domain · store + rating · age · IG followers · parent co (if any).
- **Actions:** `[watch] [note] [tag]` in header; InfoDot glossary beside GemScore + each signal
  (one-line grug definitions, cap 3–5 first-session hints).
- **Honest confidence:** when data thin → `grug not sure yet. watch few more day.`

### 5.3 Saved (`/saved`) — `SavedGems`
Replaces `Review.tsx`. Views: **list** (default) · **board** (stage kanban) · **compare**.

- **Save:** single keystroke / `[watch]` toggle, zero decisions → default watchlist. On save,
  **snapshot** `{gem_score, sv, ur, fi, active_ad_count, savedAt}` onto the record.
- **Annotate (on demand):** `TagInput` creatable combobox (normalized tags, autocomplete from prior
  tags, keyboard-driven); `NoteEditor` grug-prompted `why you save this?` in a quiet expandable
  strip (never a blocking modal).
- **Stage kanban:** columns `just saw → watching → looks real → building`; drag to advance.
- **Optional typed fields:** `verdict` (build/clone/pass/revisit — folds in old Review verdict),
  `confidence` 1–5. Opt-in, filterable, never forced.
- **Compare:** select 2–5 → side-by-side on GemScore + 3 signals + cohort + funnel/price; highlights
  (best velocity, most under-radar).
- **Bulk:** filter (tag/cohort/stage/score) → bulk tag/archive/advance.
- **States:** empty `no gem saved yet. go find one.` `[find]`; annotations empty
  `no note yet. why this gem good? write it.`

### 5.4 Signed-out entry (`/`) & login
- `/`: thin one-screen hero (one editorial display line in grug voice + short value prop + `go in`
  CTA → `/login`). Authed users redirect to `/discover`. Removes Landing's marquee/top-3/HUD.
- `/login`: restyle `LoginForm` to new tokens; copy unchanged grug (`go in`, `grug think…`).

### 5.5 First-run & activation (lightweight, all skippable)
- **First run (~60s):** welcome → pick niche/category (seeds feed) → land on Discover, top gem row 1,
  invite to `watch` + note. No tour/tooltip carousel. (If niche-pick adds scope risk, ship the
  seeded Discover + activation checklist first and treat the niche step as a fast-follow.)
- **ActivationChecklist** (dismissible, on Saved/Discover): `find gem · see why it climb · save it ·
  write note` — completes at first saved+annotated gem.

---

## 6. Save/annotate data model (client-side)

`web/src/lib/saved.ts` — localStorage key `fielddesk.saved`, replacing/migrating
`fielddesk.decisions` and `fielddesk.board`.

```ts
type SavedGem = {
  advertiserId: number
  saved: true
  savedAt: string                 // ISO
  scoreSnapshot: {                 // frozen "why it was emerging" at save time
    gemScore: number
    scalingVelocity: number | null
    underRadar: number | null
    funnelIntent: number | null
    activeAdCount: number | null
  }
  tags: string[]                   // normalized: trim + lowercase + collapse punctuation
  note: string
  stage: 'just_saw' | 'watching' | 'looks_real' | 'building'
  verdict?: 'build' | 'clone' | 'pass' | 'revisit'
  confidence?: 1 | 2 | 3 | 4 | 5
}
```
Tag registry `fielddesk.tags` `{name, uses}[]` powers autocomplete. A tiny store module exposes
`save/unsave/isSaved/setNote/addTag/removeTag/setStage/setVerdict/list` + a React hook so all screens
stay in sync. Backend persistence = explicit follow-up (out of scope).

---

## 7. Components

**Create:** `tokens` (globals.css) · `ScoreBadge` · `SignalChips` · `WhyLine` · `Sparkline`
(thin wrapper over existing `SketchLineChart`) · `SaveToggle` · `TagInput` · `NoteEditor` ·
`FilterRail` · `EmptyState` (status + 1 visual + 1 CTA) · `InfoDot`/glossary · `StageBoard` ·
`CompareView` · `ActivationChecklist` · `FirstRunNiche` · `lib/saved.ts` + `useSaved` hook ·
`lib/why.ts` (derive WhyLine/status/band/chip-strength from SV/UR/FI).

**Refactor:** `Board → DiscoverFeed` (+ `GemCard` rework) · `Dossier →` `GemVerdict / EvidenceReel /
ScalingTrend / GemFacts` · `Review →` fold into `SavedGems` · `AppHeader` nav (discover/saved/leave +
saved count) · `Landing →` thin hero · `palette.ts` to new ramp.

**Retire:** `Marquee` · `CornerHud` · `LiveClock` · `Grain` · `SessionTree` · drag-canvas logic in Board.

**Keep:** `Backdrop` · Rough.js primitives (`RoughLayer`, `SketchFrame`, `Polaroid`, `Stamp`,
`SketchLineChart`, `SketchConnector`, `GraphPaper`) · grug voice · adapter/API layer.

---

## 8. Copy (grug voice) — canonical strings

status `new spark / hot now / cooling / steady` · expand `how grug know?` · evidence `see proof` ·
push `why it climb` · pull `what hold it back` · thin `grug not sure yet. watch few more day.` ·
discover banner `these gem grug found today. look around.` · no-results `nothing match. loosen filter.` +
`put back` · saved empty `no gem saved yet. go find one.` + `find` · note prompt `why you save this?` ·
band `weak / maybe / strong / rare gem` · glossary e.g. `under radar = nobody know yet. good. you first.`
Machine-facing text (code/types/commits/docs) stays professional.

---

## 9. Build sequence

1. **Tokens & utilities** — globals.css type/space/color, palette.ts, contrast check.
2. **Chrome cleanup + routing/nav** — retire decor, add redirects, AppHeader nav, thin `/` hero.
3. **`lib/why.ts` + `lib/saved.ts` + `useSaved`** — pure logic, unit-tested.
4. **Discover feed** — GemCard, FilterRail, sort, seed/empty/no-results states.
5. **Gem detail** — GemVerdict (3 altitudes), EvidenceReel, ScalingTrend, GemFacts, InfoDot.
6. **Saved** — SaveToggle wired, TagInput, NoteEditor, StageBoard, CompareView, bulk.
7. **First-run + ActivationChecklist + glossary.**
8. **Verify** — run the app, screenshot each screen at mobile/desktop, contrast pass, key flows.

---

## 10. Acceptance criteria

- A first-time user lands on a **non-blank** Discover, sees the top gem, and can `watch` it in ≤ 2 actions.
- For any gem, the user can state **why it's emerging in one sentence** without reading numbers, and
  reach the proof (creatives/funnel) in **one click**.
- Saving is **one action**; the saved gem shows the frozen score snapshot; notes/tags are addable
  without a modal.
- No decorative chrome remains (clock/HUD/marquee/grain/DAW-tree/drag-canvas gone).
- All type/space/color come from tokens; no pure `#000/#FFF`; all grays meet contrast on their surface.
- Existing data flows (mock + live) still work; no API contract change required.
- `web/` builds, lints, and typechecks clean; logic modules (`why.ts`, `saved.ts`) unit-tested.
