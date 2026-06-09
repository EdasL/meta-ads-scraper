# Field Desk — Emerging App Finder UI

A black-and-white, hand-drawn **evidence board** for hunting hidden-gem apps
scaling on Meta ads. Gems are index cards you drag on an inked board; open one
to read its case file (ad creatives as polaroids, funnel steps wired with red
string, scaling drawn on graph paper); the review queue lets you stamp
accept / reject / hold.

Internal tool. Built with Next.js 16 (App Router) + React 19 + Tailwind 4 +
framer-motion + [rough.js](https://roughjs.com). The Python pipeline in
`../src` (the `agf` package) is the eventual data source.

## Run it

```bash
cd web
npm install
npm run dev        # http://localhost:3000
```

**Dev login:** `founder@fielddesk.local` / `fielddesk`
(change via `AUTH_DEV_PASSWORD`, or seed real users with `AUTH_USERS`).

Copy `.env.example` to `.env.local` and set `AUTH_SECRET`
(`node -e "console.log(require('crypto').randomBytes(32).toString('base64'))"`).

## Screens

| Route | What | Auth |
|---|---|---|
| `/` | TikTok-style landing: "Find validated business ideas" → scroll-snap feed of 3 proven ideas → CTA | public |
| `/login` | Sketch-styled credentials login | public |
| `/board` | Draggable evidence board of gem cards (positions persist to localStorage), cohort + min-score filters | protected |
| `/gems/[advertiserId]` | Gem case file: scaling chart, polaroid creatives, red-string funnel, company notes | protected |
| `/review` | Undecided gems ≥70; tactile accept/reject/hold stamp | protected |

Route protection is enforced by `middleware.ts` via the edge-safe
`src/lib/auth.config.ts`.

## Data: MOCK now, LIVE later

All screens read through the typed client in `src/lib/api.ts`. It has two modes,
switched by env:

```bash
NEXT_PUBLIC_DATA_MODE=mock         # default — bundled fixtures in src/lib/mock
NEXT_PUBLIC_DATA_MODE=live         # call the Plan 7 read-only FastAPI
NEXT_PUBLIC_API_URL=http://localhost:8000/api
```

The contract (`src/types/contracts.ts`) is frozen against the master plan
(`../docs/plans/00-master-plan.md`): §3.6 matview `frontend.emerging_scaling_apps`
and §3.7 review keyed on `advertiser_id`. When the pipeline (Plans 2–7) is
running, the client maps 1:1 to Plan 7 endpoints — no UI changes needed:

| Client fn | LIVE endpoint (Plan 7) |
|---|---|
| `getShortlist({cohort, min_score})` | `GET /api/shortlist?cohort=&min_score=` |
| `getGem(advertiserId)` | `GET /api/gems/{advertiser_id}` |
| `getScaling(advertiserId)` | `GET /api/gems/{advertiser_id}/scaling` |
| `getReviewQueue()` | `GET /api/review/queue` |
| `postReviewDecision(id, decision)` | `POST /api/review/{advertiser_id}/decision` |

In mock mode, review decisions persist to `localStorage` so the queue updates;
in live mode they POST to the API.

## Design system

`src/components/sketch/` — rough.js primitives: `SketchFrame`, `SketchArrow`,
`RedString`, `Stamp`, `GraphPaper`, `GemCard`, `Polaroid`, `SketchLineChart`.
Tokens live in `src/app/globals.css` (ink/paper palette, graph-paper surface,
paper grain). Strict monochrome — the only color is `--string-red`, reserved for
red-string connectors and alert stamps. Handwritten display face is Architects
Daughter (an Excalidraw "Virgil" stand-in); Geist + Geist Mono carry body/data.

## Phase 2 (not built yet)

- **Emails / password reset.** Auth is credentials-only with seeded users today.
  When email is needed, port the aesthetic of femrun's templates
  (`femrun/api/templates/emails/` — `password_reset.html`, `welcome.html`) and
  add a reset route.
- **Public signup & billing** — intentionally out of scope for the internal tool.
- **Live wiring** — flip `NEXT_PUBLIC_DATA_MODE=live` once Plan 7's FastAPI is up.
