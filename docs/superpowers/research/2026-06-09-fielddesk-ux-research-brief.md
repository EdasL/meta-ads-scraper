# Field Desk — UX Redesign Brief

> Source: 5-angle web research sweep (2026 UI guidelines · guided UX · discovery feeds ·
> score explainability · save/annotate), synthesized 2026-06-09. Drives the redesign spec.

Skin to keep: brutalist dark monochrome editorial, tight heavy type, Rough.js sketch accents,
grug voice. The job is to make that skin **rigorous** (tokenized, WCAG-checked, tabular) — not
to soften it.

---

## 1. Typography & spacing system

Anchor body at **14px**, not 16px — this is a dense scanning tool, not a marketing page. One
accent only; hierarchy via weight + gray-shade, never color.

**Type scale** (px / rem @ 16px root, line-height, tracking):

| token | px | rem | line-height | tracking | use |
|---|---|---|---|---|---|
| `text-xs` | 12 | 0.75 | 16px (1.33) | +0.2px | grug labels, signal captions, meta |
| `text-sm` | 13 | 0.8125 | 18px (1.38) | 0 | secondary table rows, card sublines |
| `text-base` | 14 | 0.875 | 21px (1.5) | -0.006em | body / UI / primary rows |
| `text-md` | 16 | 1.0 | 24px (1.5) | -0.01em | prose / annotation editor only |
| `text-lg` | 20 | 1.25 | 26px (1.3) | -0.02em | card titles, gem names |
| `text-xl` | 24 | 1.5 | 29px (1.2) | -0.03em | GemScore number on card |
| `text-2xl` | 32 | 2.0 | 36px (1.12) | -0.04em | GemScore on detail, section heads |
| `text-3xl` | 48 | 3.0 | 50px (1.05) | -0.06em | editorial display ("FIND IDEA THAT ALREADY WORK") |
| `text-4xl` | 64 | 4.0 | 64px (1.0) | -0.06em | hero display only |

**Rules:** display sizes (32+) are rationed to brutalist editorial moments; everything in data
views lives at 12–20px. Weights never below 400; UI text **510**, headings ~590, max 2–3 faces.
Small-caps labels get **positive** tracking; display gets negative. Prose/annotation measure
**45–75ch** (~34em cap). All metrics use `font-variant-numeric: tabular-nums`, right-aligned in
columns.

**Spacing scale** (8pt + 4px half-step, micro 2px): `2 / 4 / 8 / 12 / 16 / 24 / 32 / 40 / 48 / 56`.
Dense zones (card interiors, table rows) use **4 / 8 / 12**; reserve **24 / 32** for gaps between
bento sections only.

**Color (dark monochrome):**
- Canvas near-black `#0D0D0F` (never `#000` — halation). Off-white text `#EDEDED` (never `#FFF`).
- Neutral ramp: `#0A0A0A / #171717 / #262626 / #404040 / #525252 / #737373 / #A3A3A3 / #D4D4D4 / #E5E5E5 / #F7F7F7`.
- Surface ladder by lightness, **lighter = higher**, no drop shadows: canvas L11% → sidebar L15%
  → card L18% (1px border `#262626`) → expanded/hover L22% → drawer/modal L25% → popover L28%.
- **One** rationed accent for primary action/active state. GemScore is the chromatic signal — keep
  it the only color that varies; status (saved/new/cooling) stays functional and muted.
- Cards = 1px inset borders, not fills/shadows. Border-radius small (2–4px) — no pills.

**Contrast targets (check against the actual surface, not canvas):** body ≥ 4.5:1; large text
(≥24px or ≥18.66px bold) & icons/borders that carry state ≥ 3:1. Secondary gray text must still
clear 4.5:1 — the #1 dark-theme failure is grug labels dropped too dim. Visible focus rings required.

---

## 2. Discovery feed

**Recommendation: a hybrid card grid (bento), not a table, not a bare thumbnail wall.** Candidates
are both visual (funnel/ad screenshot) and moving (velocity). Each gem = one bordered bento block.
(A Sensor-Tower-style 400-field table is the explicit anti-pattern for solo founders.)

**Per-card anatomy (default / collapsed):**
- **GemScore** — the hero: largest element (`text-xl` 24px tabular), top-left. The list sorts by it.
- **Status chip** (one grug word): `new spark` / `hot now` / `cooling` / `steady`.
- **Trend arrow + %** beside the score (`↗ +180%`).
- **Funnel/ad screenshot** thumbnail as the visual anchor.
- **One-line grug "what + why"**: e.g. `app sell sleep sound. spend 4x in 14 day. nobody copy yet.`
- **Rough.js sparkline** of active-ad-count / spend over last 14–30 days (the hockey-stick is the pitch).
- **Three signal mini-chips**: `scaling fast` · `under radar` · `real funnel` (filled vs hollow by strength — see §3).
- **One-click `watch` button** (dominant on hover/focus; see §5).
- Freshness, honest: `first seen 3 day ago`.

**Default sort:** GemScore **descending** — top gem is row 1.
**Default view:** a pre-applied **`rising this week`** filter, not the unfiltered firehose. Offer
saved views (`my niche`, `under 30 day old`).
**Filter rail — short and opinionated, 4–6 axes max** behind a grug `more filter` toggle: GemScore
band, scaling velocity, under-radar threshold, has-funnel, category, **ad-age (`ads now > N day`)**.
Ad-age is a free, public, durable winner-proxy — surface it.
**Flagging new/rising:** one badge per card from the status taxonomy, in the monochrome+accent
palette (never a rainbow). Optionally split the feed by momentum: primary `rising` feed, quieter
`cooling / already big`. Default founder to movers.

---

## 3. Explaining WHY it's emerging

Render GemScore at **three altitudes**; never dump weighted bars up front (over-explaining *lowers*
trust). Plain grug language is a trust *advantage* — short natural-language reasons beat technical
breakdowns.

**Altitude 1 — verdict (always on card):** big number + status chip + trend arrow.
`GemScore 82 ↗ hot now`. Use named bands, not raw cutoffs: `0–49 weak · 50–69 maybe · 70–84 strong · 85+ rare gem`.

**Altitude 2 — the "Because" line (one sentence, names the dominant signal first):**
- Velocity-led: `grug think hot — ad spend go up fast, 4x in two week.`
- Under-radar-led: `big spend but almost nobody run ad like this yet. you early.`
- Funnel-led: `real checkout, real price, real upsell. they make money, not just grab email.`

**Altitude 3 — push/pull breakdown (on `how grug know?` expand):** the three signals as a
**push-and-pull** layout, not three neutral bars. A strong signal pushes up (filled / heavy ink); a
weak one is an honest **pull-down** (hollow / Rough.js hatching). Direction encoded by fill-weight +
hatching, not hue — keeps monochrome.

**Component recommendation per signal:**
- **Scaling Velocity** → Rough.js **sparkline** + arrow + %. Must be a *curve* (a bare number is
  meaningless without "compared to what"). Never a lone integer.
- **Under Radar** → bar shown **against a baseline** (competitor count / category avg), e.g.
  `only 3 advertisers run this · category avg 40`. Show `how many run this angle now` for urgency.
- **Funnel Intent** → **chips/checklist** of detected steps: `landing ✓ · checkout ✓ · price ✓ · upsell ✓`.
- **Composite** → small dial/gauge framing the number against 0–100, each sub-signal a contributing
  bar. Order bars by **current contribution**, not fixed order; tag each `big push / some push / small push`.

**Evidence-first expansion (`see proof`):** founders trust the artifact, not the math. Show the
actual Meta ad creatives, the spend/ad-count sparkline, and the funnel steps as a "highlight reel"
where each Funnel-Intent claim **links to the exact screenshot** that proved it.

**Counterfactual + honest confidence:** `would be bigger gem if more spend, or if still secret.`
When data is thin: `grug not sure yet. watch few more day.`

**Microcopy bank:** status = `new spark / hot now / cooling / steady`; expand = `how grug know?`;
evidence = `see proof`; push = `why it climb`; pull = `what hold it back`; thin = `grug not sure yet. watch few more day.`

---

## 4. Guiding the user

Guide by default-state and example, **not tours**. Linear's anti-onboarding (~60s, all skippable)
is the model; the brutalist+grug skin already strips chrome — lean into learn-by-doing.

**First run (~60s, everything skippable):** welcome → pick niche/category (seeds the feed) → land
on the board with the top gem at row 1 and an invite to `watch` + note. No tour, no tooltip carousel.

**Discover screen:**
- **Never blank.** Seed 3–6 real/realistic gems sorted GemScore-desc with a grug banner:
  `these gem grug found today. look around.` The seed *is* the demo. Label synthetic data honestly.
- Default to the `rising this week` curated view; advanced filters hidden behind `more filter`.
- **No-results state:** status + one Rough.js sketch + single reset CTA — `nothing match. loosen filter.` [put back].

**Gem detail screen:**
- Progressive disclosure: collapsed = score + chip + one-line because. Expand reveals push/pull +
  sparklines; `see proof` reveals evidence reel.
- **Inline help for proprietary terms** — info dot beside GemScore and each signal opens a one-line
  grug glossary: `under radar = nobody know yet. good. you get there first.` Cap first-session hints at 3–5.

**Saved screen:**
- **Empty state:** `no gem saved yet. go find one.` [find].
- **Annotations empty:** `no note yet. why this gem good? write it.`
- Activation **checklist** tied to the aha: `find gem` / `see why it climb` / `save it` / `write note`
  — completes at first saved+annotated gem; dismissible.

Empty states = exactly **3 elements** (status line + one visual + single CTA), capped at 2–3 sentences.

---

## 5. Save & annotate

Model: **capture in one keystroke, structure on demand.** Never block the save on a decision.

**Save action:** a single blunt `watch` / `save` toggle on every card — **zero decisions**, drops the
gem into a default **watchlist**. No list-picker, no tag prompt on the primary path.

**Auto-snapshot WHY at save time (non-negotiable for Field Desk):** freeze GemScore + the three
signals + active-ad count **as of that moment** onto the saved record. Captures "why it was emerging"
before the metric moves — the whole premise — and the worker keeps **appending score snapshots** so
the saved gem becomes the founder's "I called it early" timeline.

**Two open extension slots per gem (cover ~90% of needs, zero schema):**
- **tags** — `string[]`, normalized (trim + lowercase + strip punctuation) to kill near-dupes.
- **note** — freeform, grug-prompted `why you save this?`.

**Tag input:** creatable autocomplete combobox — suggests past tags, `add new tag "x"` inline,
keyboard-driven. Annotations render in a **quiet right margin / expandable strip**, never a blocking modal.

**Keyboard-first triage:** focus a card → `S` save · `T` tag · `N` note.

**Pipeline (small fixed stages, kanban-over-records):** `just saw → watching → looks real → building`.
Stage is one attribute; same gems also viewable as the feed. No arbitrary folders.

**Optional typed fields (opt-in only):** `verdict` single-select (build / clone / pass / revisit) and
`confidence` 1–5 — filterable, never forced.

**Comparison view:** select **2–5** watched gems → line up on GemScore + 3 signals + niche + price/funnel
type; reshape to highlights (best velocity, most under-radar).

**Bulk actions:** filter watchlist → bulk-tag / archive / advance-stage in one sweep.

---

## 6. Anti-patterns to avoid

- **Pure `#000` + `#FFF`** — halation/eye strain. Use `#0D0D0F` / `#EDEDED`.
- **16px body in data views** — wastes density. 14px (12–13px rows).
- **Drop shadows for depth** in dark mode — invisible. Use lighter surfaces + 1px borders.
- **Secondary gray text below 4.5:1** — the most common dark-theme WCAG fail.
- **400-field enterprise table / wall-of-data** — kills scannability for solo founders.
- **Blank first run** — drives day-one churn. Always seed.
- **Overstuffed empty states** — one idea, one CTA.
- **Jargon-only signal labels** — pair with a plain grug one-liner.
- **All weighted sub-scores/formulas up front** — over-explaining lowers trust.
- **Bare numbers with no baseline/trend** — always pair with sparkline or vs-category.
- **Proportional numerals in metric columns** — always tabular-nums.
- **Requiring destination/tags at save** — biggest friction killer.
- **Free-form tags with no normalization** — fragments filtering.
- **Annotation in a modal** that hides the gem — breaks triage context.
- **Rainbow of badges, glassmorphism/3D/kinetic gradients** — fights the brutalist-editorial skin.
- **Stale data masquerading as fresh** — breaks the "before the market notices" promise.
