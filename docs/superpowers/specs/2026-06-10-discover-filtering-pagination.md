# Spec — Discover backend filtering, categories, pagination (2026-06-10)

Goal (user): proper filtering with custom dropdowns and good UX in search; a real
categories filter; pagination; an animation that shows filters changing results;
filtering happens on the backend.

Standing constraints: US-only apps-only emerging feed (≤50 active ads), light
paper theme, plain sentence-case copy, every UI element grounded in real data.

## Backend (Go, `backend/`)

### `GET /api/shortlist` — extended params

All existing params keep their exact behavior (`cohort`, `min_score`, `limit`
default 50, `offset` default 0). New, all optional:

| param        | type   | semantics |
|--------------|--------|-----------|
| `category`   | string | `EXISTS (apps.id = app_id AND apps.genre = ?)` — genre from `public.apps` |
| `q`          | string | `app_name ILIKE %q% OR page_name ILIKE %q%` (`%`/`_`/`\` escaped) |
| `rising`     | bool   | hide falling: keep when `latest_count IS NULL OR first_count IS NULL OR latest_count >= first_count` (mirrors old client rule) |
| `has_funnel` | bool   | `funnel_intent >= 0.5` |
| `min_ad_age` | int    | when > 0: `max_longevity_days >= ?` (NULL excluded) |
| `sort`       | enum   | `gemscore` (default) → `gem_score DESC`; `fastest` → `scaling_velocity DESC NULLS LAST`; `newest` → `max_longevity_days ASC NULLS LAST`; every order tie-broken by `advertiser_id` |

Bool params accept `true/1/false/0`; bad bool, bad int, non-finite float,
negative limit/offset, or unknown `sort` → 422 `{"detail": ...}` (FastAPI
style); a present-but-empty string param means "no filter"; `limit` clamps at
500. Body stays a bare `ShortlistRow[]` (parity safe). New response header
**`X-Total-Count`**: count over the same WHERE (including the has-creative
EXISTS) without limit/offset — computed in the same READ ONLY tx (READ
COMMITTED: a concurrent matview refresh between the two statements can skew
the total transiently; self-corrects on the next fetch). CORS: add
`X-Total-Count` to `ExposeHeaders`.

Repo signature becomes `Shortlist(ctx, g, ShortlistParams) (rows, total, err)`
with conditional WHERE building (drops the `CAST(? AS text) IS NULL` port
idiom for the new params).

### `GET /api/shortlist/categories` — new

`[{"category": "Health & Fitness", "count": 12}, ...]` — distinct
`public.apps.genre` over visible matview rows (same has-creative EXISTS), NULL
genres skipped, `ORDER BY count DESC, category ASC`. Accepts the same filter
axes as the list (minus `category` itself, which is ignored) so each count
equals what picking that category would yield under the active filters — a
dropdown number must never disagree with the click it invites. Powers the
dropdown; never hardcoded.

### Tests (integration, agf_it)

Seed grows a 3rd advertiser: app genre `Health & Fitness`, cohort
`Health & Fitness×US`, gem_score 72, scaling_velocity 0.9, longevity 12,
funnel_intent 0.2, falling growth (10→4) via `adtech.advertiser_growth` (add to
TRUNCATE). Existing assertions updated (3 rows; rows[1] stays adv2 at 75).
New tests: category filter, q (app + page name), rising hides faller,
sort=newest/fastest order, min_ad_age=21, has_funnel, X-Total-Count with
limit=1 and with filters, categories endpoint shape/order, 422 on bad
sort/bool.

## Web data layer (`web/src/`)

- `contracts.ts`: `ShortlistQuery` gains `category? q? rising? has_funnel?
  min_ad_age? sort? limit? offset?`; new `CategoryCount`, `ShortlistPage
  { rows, total }`.
- `api.ts`: `shortlistParams(query)` exported pure builder; `getShortlist`
  returns `ShortlistPage` (reads `X-Total-Count`, falls back to `rows.length`);
  `getCategories()` → `/shortlist/categories`.
- `lib/pagination.ts`: `pageItems(page, pageCount)` → `(number|"gap")[]`
  (1 … window ±1 … last). Unit tests (node --test) for both pure helpers.

## UI (`web/src/components/`)

- **`ui/Dropdown.tsx`** — custom select: trigger styled like the existing
  `control` (mono, surface-3, 1px line), chevron; panel = paper bg, ink border,
  pop-in 120ms; full listbox keyboard semantics (arrows/Home/End/Enter/Esc),
  click-outside (InfoDot idiom), check glyph on selected, muted right-aligned
  `hint` (category counts).
- **FilterRail** — category Dropdown (options from `/shortlist/categories`,
  labels `Genre (count)`) REPLACES the cohort "Kind" select (cohort =
  genre×US ⇒ redundant); sort + ad-age become Dropdowns; debounced (300ms)
  search input with clear button in the main row. Filters interface: `view,
  sort, category, q, minScore, hasFunnel, minAdAge`.
- **Pagination** — page size 24; `‹ 1 … 4 [5] 6 … 12 ›` + "25–48 of 137"
  range caption; hidden when one page; page change scrolls to top.
- **DiscoverFeed** — drops `applyFilters`/`SORTERS` (server does it). State
  lives in URL searchParams (defaults omitted), `router.replace(..., {scroll:
  false})`; `useSearchParams` ⇒ `<Suspense>` wrapper in `discover/page.tsx`.
  Fetch per query with AbortController; first load = skeleton grid; refetch =
  keep old grid dimmed (`.is-refreshing`: opacity .45 + desaturate) under a
  2px scanning bar; results re-enter with staggered fade-rise (per-card
  `animation-delay`, grid keyed by query). Auto-reset to page 1 when a
  narrowed result set strands the page. Count line uses `total`.
- **globals.css** — `gem-in`, `pop-in`, `feed-scan`, `skel-shimmer` keyframes +
  helper classes; all disabled under `prefers-reduced-motion`.

## Out of scope

Python FastAPI fallback (env-gated parity suite tests only existing behavior),
Saved page, review queue.
