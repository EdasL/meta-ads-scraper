# Saved board: board-only + custom groups — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/saved` becomes a board-only page whose columns are user-defined groups (seeded from the old fixed stages), with overflow-proof cards; list and compare views are deleted.

**Architecture:** New `frontend.saved_groups` table + `group_id` on `frontend.saved_gems` (migration 160 with one-time stage→group backfill). New Go resource `/api/saved-groups` (identity from KeyAuth token, hunt idiom). Web store `saved.ts` gains a groups slice; `StageBoard` is replaced by `GroupBoard` keyed on `group_id`.

**Tech Stack:** Postgres (raw SQL migrations, lexical order, applied by `src/agf/db/migrate.py`), Go (gin + gorm raw SQL repos), Next 16 / React 19 / TS, inline `React.CSSProperties` + global utility classes, node --test for pure web helpers, Go integration tests against `AGF_TEST_DB_URL`.

**Spec:** `docs/superpowers/specs/2026-08-03-saved-board-groups-design.md` — read it first.

## Global Constraints

- Product copy: plain, clear, professional. No marketing fluff, no exclamations. Product nouns intact: **Field Desk**, **GemScore**, **cohort** (project CLAUDE.md).
- Web code: **Next 16 has breaking changes** — read the relevant guide in `web/node_modules/next/dist/docs/` before writing Next-specific code (web/AGENTS.md).
- Migrations: files `db/schema/NNN_name.sql` applied in lexical order; next number is **160**. A bare `%` in SQL breaks `apply_migrations` — use `mod()` if needed. Go never AutoMigrates.
- All commits on main, no feature branches. Commit after each task with pathspec limits.
- Test DB: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it`. ONE test consumer at a time; full-suite zero-failed gate runs last, solo, bare (no pipes).
- `stage` column and `useSavedList` export surface must keep working: `AppHeader.tsx`, `ActivationChecklist.tsx`, and `repositories.MySavedGems` (hosted MCP, `backend/repositories/member_repo.go:95`) depend on them.
- Styling stays in the existing idiom: inline `React.CSSProperties`, CSS vars (`--ink`, `--paper`, `--line`, `--muted`, `--surface-3`, `--lh-sm`), utility classes (`.t-sm`, `.t-xs`, `.eyebrow`, `.w-strong`, `.w-heavy`, `.nums`, `.font-mono`). No new CSS system, no dnd library.
- Motion: 150–200ms, ease-out, transform/opacity only, `@media (prefers-reduced-motion: reduce)` respected. No entrance choreography.

---

### Task 1: Migration 160 — saved_groups + group_id + backfill

**Files:**
- Create: `db/schema/160_saved_groups.sql`
- Reference: `db/schema/155_hunts.sql` (style), `db/schema/102_frontend_saved_gems.sql` (the table being altered)

**Interfaces:**
- Produces: table `frontend.saved_groups (id BIGSERIAL PK, user_email TEXT NOT NULL, name TEXT NOT NULL, position INT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now())`, index `ix_saved_groups_user (user_email, position)`, column `frontend.saved_gems.group_id BIGINT NULL REFERENCES frontend.saved_groups(id) ON DELETE SET NULL`.

- [ ] **Step 1: Write the migration**

```sql
-- 160_saved_groups.sql — user-defined board columns for /saved.
-- Groups replace the fixed stage columns on the board; saved_gems.stage stays
-- (legacy readers: hosted MCP my_saved_gems).

CREATE TABLE frontend.saved_groups (
  id         BIGSERIAL PRIMARY KEY,
  user_email TEXT NOT NULL,
  name       TEXT NOT NULL,
  position   INT  NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_saved_groups_user ON frontend.saved_groups (user_email, position);

ALTER TABLE frontend.saved_gems
  ADD COLUMN group_id BIGINT REFERENCES frontend.saved_groups(id) ON DELETE SET NULL;

-- One-time backfill: each existing user gets 4 groups mirroring the stage
-- pipeline, and every saved gem lands in the group matching its stage.
-- Idempotent: skips users who already have groups.
WITH stage_names(stage, name, position) AS (
  VALUES ('just_saw', 'Just saw', 0),
         ('watching', 'Watching', 1),
         ('looks_real', 'Looks real', 2),
         ('building', 'Building', 3)
),
users AS (
  SELECT DISTINCT user_email FROM frontend.saved_gems
  WHERE user_email NOT IN (SELECT DISTINCT user_email FROM frontend.saved_groups)
)
INSERT INTO frontend.saved_groups (user_email, name, position)
SELECT u.user_email, s.name, s.position FROM users u CROSS JOIN stage_names s;

-- CTEs don't span statements; the UPDATE re-joins through a VALUES mapping.
UPDATE frontend.saved_gems sg
SET group_id = gr.id
FROM frontend.saved_groups gr,
     (VALUES ('just_saw', 'Just saw'),
             ('watching', 'Watching'),
             ('looks_real', 'Looks real'),
             ('building', 'Building')) AS m(stage, name)
WHERE sg.group_id IS NULL
  AND sg.stage = m.stage
  AND gr.user_email = sg.user_email
  AND gr.name = m.name;
```

- [ ] **Step 2: Apply to the integration DB and verify**

Run (from repo root; test DB must be up):
`AGF_DATABASE_URL=postgresql://agf:agf@localhost:55433/agf_it python -m agf.db.migrate` (check `src/agf/db/migrate.py` for the exact entrypoint/env name before running — copy how the test harness invokes it).

Then verify by SQL: insert a fake user's gem with `stage='watching'` BEFORE applying on a scratch check is overkill — instead assert structurally:
`psql postgresql://agf:agf@localhost:55433/agf_it -c "\d frontend.saved_groups"` shows the table; `-c "SELECT column_name FROM information_schema.columns WHERE table_schema='frontend' AND table_name='saved_gems' AND column_name='group_id'"` returns 1 row. Backfill correctness is asserted properly in Task 2's integration test (which inserts gems, deletes groups, re-runs the backfill statements? no — it seeds via the repo). Structural check suffices here; the prod backfill is verified at deploy.

- [ ] **Step 3: Commit**

```bash
git add db/schema/160_saved_groups.sql
git commit -m "feat(saved): migration 160 — saved_groups table + saved_gems.group_id + stage backfill" -- db/schema/160_saved_groups.sql
```

---

### Task 2: Go models + repositories for saved groups

**Files:**
- Modify: `backend/models/models.go` (SavedGemRow/SavedGemIn around lines 411–458; add group models)
- Create: `backend/repositories/saved_groups_repo.go`
- Modify: `backend/repositories/saved_gems_repo.go` (upsert + list carry group_id)
- Test: `backend/internal/integration/saved_groups_repo_test.go` (copy DB idiom from `backend/internal/integration/member_repo_test.go:37-84`)

**Interfaces:**
- Consumes: Task 1 schema.
- Produces (later tasks call exactly these):

```go
type SavedGroupRow struct {
    ID       int64  `gorm:"column:id" json:"id"`
    Name     string `gorm:"column:name" json:"name"`
    Position int    `gorm:"column:position" json:"position"`
}
// SavedGemRow gains: GroupID *int64 `gorm:"column:group_id" json:"group_id"`
// SavedGemIn  gains: GroupID *int64 `json:"group_id"`

func ListSavedGroups(ctx, g, userEmail string) ([]models.SavedGroupRow, error) // seeds 4 defaults if user has none
func CreateSavedGroup(ctx, g, userEmail, name string) (models.SavedGroupRow, error) // position = max+1
func UpdateSavedGroup(ctx, g, userEmail string, id int64, name *string, position *int) (bool, error) // false = not found/not owner
func DeleteSavedGroup(ctx, g, userEmail string, id int64) (deleted bool, lastGroup bool, err error)  // reassigns gems to user's first remaining group
```

- [ ] **Step 1: Write failing integration tests** (`saved_groups_repo_test.go`): (a) `ListSavedGroups` on a fresh email returns exactly 4 groups named "Just saw"/"Watching"/"Looks real"/"Building" positions 0–3, and calling it twice doesn't duplicate; (b) `CreateSavedGroup` appends at max+1; (c) `UpdateSavedGroup` with the wrong email returns false; (d) `DeleteSavedGroup` moves that group's gems (insert 2 gems with its id first) to the user's position-0 group and returns deleted=true; (e) deleting until one remains returns lastGroup=true, deleted=false; (f) upsert+list of a saved gem round-trips `group_id`. Tests clean their rows in-txn (hermetic).
- [ ] **Step 2: Run to verify failure** — `go test ./internal/integration/ -run SavedGroups -v` (bare, from `backend/`; expect compile/undefined failures).
- [ ] **Step 3: Implement.** Raw-SQL funcs in `saved_groups_repo.go` using `db.WithReadTx`/`db.WithWriteTx`. Seeding must be race-safe: `INSERT … SELECT … WHERE NOT EXISTS (SELECT 1 FROM frontend.saved_groups WHERE user_email = ?)` inside the write tx, then read. Delete: in one write tx — check count of user's groups (>1 else lastGroup), `UPDATE frontend.saved_gems SET group_id = (first remaining group id) WHERE group_id = ?`, then `DELETE … WHERE id = ? AND user_email = ?`. Extend `UpsertSavedGem` SQL: add `group_id` to the INSERT column list/VALUES and `group_id = EXCLUDED.group_id` to the DO UPDATE SET; add it to the `ListSavedGems` SELECT list. Validate ownership of a non-nil `GroupID` in the upsert (`SELECT 1 FROM frontend.saved_groups WHERE id=? AND user_email=?` in the same tx; on miss return a sentinel error the controller maps to 400).
- [ ] **Step 4: Run tests to pass** — same command, bare. Also `go build ./...` and `go vet ./...`.
- [ ] **Step 5: Raw-key wire test** (cross-fence rule): marshal a `SavedGroupRow` and a `SavedGemRow` with GroupID set, unmarshal into `map[string]any`, assert literal keys `"id"`, `"name"`, `"position"`, `"group_id"` exist. Put it next to the integration test or in models tests if a models test file exists.
- [ ] **Step 6: Commit**

```bash
git add backend/models/models.go backend/repositories/saved_groups_repo.go backend/repositories/saved_gems_repo.go backend/internal/integration/saved_groups_repo_test.go
git commit -m "feat(saved): saved_groups repo + group_id on saved-gems rows" -- backend/models/models.go backend/repositories/saved_groups_repo.go backend/repositories/saved_gems_repo.go backend/internal/integration/saved_groups_repo_test.go
```

---

### Task 3: Go controller + routes for /api/saved-groups

**Files:**
- Create: `backend/controllers/saved_groups_controller.go`
- Modify: `backend/routers/routers.go` (register in `Init`, lines ~22–83, next to saved-gems)
- Reference: the hunt controller for the `UserFrom`/KeyAuth identity idiom and 404-on-ownership-miss; `saved_gems_controller.go` for shape.

**Interfaces:**
- Consumes: Task 2 repo functions (exact signatures above).
- Produces routes (all under the existing `/api` KeyAuth group; writes also `middlewares.RequireToken()` like saved-gems writes):
  - `GET  /api/saved-groups` → `200 [SavedGroupRow]`
  - `POST /api/saved-groups` body `{"name": string}` → `200 SavedGroupRow`; empty/whitespace name → 422
  - `POST /api/saved-groups/:id/update` body `{"name"?: string, "position"?: int}` → `200 {"status":"ok"}`; unknown id or foreign owner → 404
  - `POST /api/saved-groups/:id/remove` → `200 {"status":"ok"}`; last group → `409` detail "Keep at least one group."
- Identity: derive the email server-side exactly the way the hunt controller does (`UserFrom(ctx)` or equivalent — read the hunt controller and copy it; NEVER a query param).

- [ ] **Step 1:** Read the hunt controller + `routers.go` registration; write the controller with the four handlers mapping repo results to the statuses above (400 with detail "group not found." from the upsert-ownership sentinel is Task 2's concern — here map repo `(false, …)` returns to 404, `lastGroup` to 409).
- [ ] **Step 2:** Register routes in `routers.Init`.
- [ ] **Step 3:** `go build ./... && go vet ./...` bare, from `backend/`. If the repo has controller-level tests idiom, add one for the 409; if not (recon says none exist for saved), the integration coverage from Task 2 + live gate covers it — don't invent a new test harness.
- [ ] **Step 4: Commit**

```bash
git add backend/controllers/saved_groups_controller.go backend/routers/routers.go
git commit -m "feat(saved): /api/saved-groups endpoints (list seeds defaults, create, update, remove)" -- backend/controllers/saved_groups_controller.go backend/routers/routers.go
```

---

### Task 4: Web store — groups slice + API client

**Files:**
- Modify: `web/src/types/api.ts` (mirror types near ApiSavedGem, lines ~234–259)
- Modify: `web/src/lib/api.ts` (client calls near saved-gems, lines ~180–190)
- Modify: `web/src/lib/saved-core.ts` (pure group helpers)
- Modify: `web/src/lib/saved.ts` (groups state + mutations)
- Modify: `web/src/lib/useSaved.ts` (hooks/re-exports — read it first; recon didn't quote it)
- Test: `web/src/lib/saved.test.ts` (extend; node --test, pure helpers only)

**Interfaces:**
- Consumes: Task 3 endpoints.
- Produces (Task 5 imports exactly these):

```ts
// saved-core.ts
export interface SavedGroup { id: number; name: string; position: number }
export function sortGroups(groups: SavedGroup[]): SavedGroup[]            // by position, then id
export function bucketByGroup(gems: SavedGem[], groups: SavedGroup[]): Map<number, SavedGem[]>
// NULL/unknown group_id → first group's bucket (spec rule). Gems sorted savedAt DESC inside a bucket.
// SavedGem gains: groupId: number | null

// saved.ts (stateful)
export function listGroups(): SavedGroup[]
export function createGroup(name: string): void      // optimistic temp id (negative), reconciled on persist response
export function renameGroup(id: number, name: string): void
export function moveGroupLeft(id: number): void      // swaps position with neighbor, persists both
export function moveGroupRight(id: number): void
export function deleteGroup(id: number): void        // optimistic: reassign its gems to first remaining group locally
export function setGroup(advertiserId: number, groupId: number): void  // rides the saved-gem upsert
// subscribe/getSnapshot extended so the board re-renders on group changes
// (either fold groups into the existing snapshot object or add a parallel
// subscribeGroups/getGroupsSnapshot pair — follow whichever keeps
// useSavedList's return type UNCHANGED; AppHeader/ActivationChecklist depend on it)
```

- [ ] **Step 1:** Types + client: `ApiSavedGroup {id, name, position}`; `ApiSavedGem`/`ApiSavedGemIn` gain `group_id: number | null`. Client funcs `getSavedGroups()`, `createSavedGroup(name)`, `updateSavedGroup(id, patch)`, `removeSavedGroup(id)` following the `live<T>()` idiom at `api.ts:180-190`. Note: the new endpoints take no `user_email` param — identity is the token.
- [ ] **Step 2 (TDD):** failing node --test cases in `saved.test.ts` for `sortGroups` (position then id), `bucketByGroup` (three gems: matching group, null groupId → first bucket, unknown groupId → first bucket; savedAt DESC order inside a bucket; empty groups list → empty map).
- [ ] **Step 3:** Implement core helpers; run `node --test` per the existing test setup (check how `saved.test.ts` is executed — package.json scripts) until green.
- [ ] **Step 4:** Stateful store: `groups` array + `groupsHydratedKey` mirroring the gems pattern (`hydrate()` also fetches `getSavedGroups()` — one extra request, acceptable); optimistic mutations with `persist` + `onSyncError` rollback-by-rehydrate exactly like gems. `fromApi`/`toUpsert` carry `group_id`. `setStage` stays exported (unused by board after Task 5, but keep the export until nothing imports it — check with grep, delete if orphaned).
- [ ] **Step 5:** `npx tsc --noEmit` (or the repo's typecheck script) + node --test bare, from `web/`.
- [ ] **Step 6: Commit**

```bash
git add web/src/types/api.ts web/src/lib/api.ts web/src/lib/saved-core.ts web/src/lib/saved.ts web/src/lib/useSaved.ts web/src/lib/saved.test.ts
git commit -m "feat(saved): groups state in the saved store + /saved-groups client" -- web/src/types/api.ts web/src/lib/api.ts web/src/lib/saved-core.ts web/src/lib/saved.ts web/src/lib/useSaved.ts web/src/lib/saved.test.ts
```

---

### Task 5: Board-only page + GroupBoard + overflow fix

**Files:**
- Create: `web/src/components/saved/GroupBoard.tsx`
- Modify: `web/src/components/saved/SavedGems.tsx` (board-only; drop ViewSwitch/compare/selection)
- Delete: `web/src/components/saved/SavedCard.tsx`, `web/src/components/saved/CompareView.tsx`, `web/src/components/saved/StageBoard.tsx`
- Reference: deleted `SavedCard.tsx:34-53` clamp pattern (grab it before deleting)

**Interfaces:**
- Consumes: Task 4 store exports (`listGroups` via a hook, `bucketByGroup`, `createGroup`, `renameGroup`, `moveGroupLeft/Right`, `deleteGroup`, `setGroup`).

**Board structure** (existing visual language: 1.5px `--ink` borders, `--paper` cards, eyebrow column headers):

- [ ] **Step 1: GroupBoard.** Grid `gridTemplateColumns: repeat(${groups.length + 1}, minmax(200px, 280px))` (+1 = the New group rail), `gap: 14`, `overflowX: "auto"`, `alignItems: "start"`. Columns from `sortGroups(listGroups())`; buckets from `bucketByGroup`.
- [ ] **Step 2: Card overflow fix.** Card header row:

```jsx
<div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "baseline" }}>
  <Link href={`/gems/${g.advertiserId}`} className="t-sm w-strong"
    style={{
      color: "var(--ink)", textDecoration: "none", flex: 1, minWidth: 0,
      display: "-webkit-box", WebkitBoxOrient: "vertical", WebkitLineClamp: 2,
      overflow: "hidden", wordBreak: "break-word",
    }}>
    {g.appName ?? g.pageName}
  </Link>
  <span className="t-md w-heavy nums" style={{ flexShrink: 0 }}>{Math.round(g.scoreSnapshot.gemScore)}</span>
</div>
```

No reserved minHeight (board cards vary; the clamp alone stops the blowout). Every other text node in the card gets `minWidth: 0` on its flex parent if in a flex row.
- [ ] **Step 3: Column header.** Name renders as text; clicking it swaps to an `<input>` (same font classes, no border-radius surprises) — Enter/blur commit `renameGroup`, Esc cancels, empty name reverts. Count in `.nums`. A ⋯ button (same `moveBtn` style as chevrons) opens a small menu (absolute-positioned within a `position: relative` header — the board scrolls horizontally, so render the menu with `position: fixed` anchored via `getBoundingClientRect` OR keep `overflow` off the column wrapper; simplest correct: attach the menu inside the header with `position: absolute` and give the grid `overflowX: auto` only — test that the menu isn't clipped; if clipped, switch to fixed). Menu items: "Move left", "Move right" (disabled at ends), "Delete group". Delete → `window.confirm` with exact copy: `Delete "<name>"? Its cards move to "<first group name>".` (skip confirm when the column is empty). If the API rejects with 409, the store's rehydrate restores state — surface `alert` with the server detail only if the deletion visibly bounced (keep it simple; no toast system exists).
- [ ] **Step 4: Card movement.** Keep ‹ › chevrons (adjacent column by position; disabled at ends, same styles as today). Add a "Move to" `<select>` styled like the existing tag `<select>` (`selectStyle` in SavedGems) rendered between the chevrons when `groups.length > 2`, listing other groups; onChange → `setGroup`. (A native select is the plain, keyboard-friendly, zero-new-idiom choice — no custom popover.)
- [ ] **Step 5: New group rail.** Last grid column: a single button, first-class size (matches the surface's primary buttons — `padding: "10px 14px"`, 1.5px `--ink` border, `.t-sm .w-strong`), label **"New group"**. Click → `createGroup("New group")` then immediately put that column header into rename mode (autofocus, text selected). Follow-through: the created column scrolls into view (`scrollIntoView({inline: "nearest", behavior: "smooth"})`, gated by prefers-reduced-motion).
- [ ] **Step 6: SavedGems.tsx board-only.** Remove ViewSwitch/View type/selected/compareGems/toggleSelect. Keep: header ("My gems", "N saved."), tag filter (unchanged), `EmptyState` for zero saves (unchanged copy). Body = `<GroupBoard gems={filtered} />`. Empty column body: `.t-xs` muted "No gems here yet." replacing the "—" placeholder.
- [ ] **Step 7: Motion.** Menu open: 150ms ease-out fade+`scale(0.97)→1` via CSS transition (no keyframes), `transform-origin` at the trigger corner. Card column-move: none (list reflow; do not animate layout). Button presses: `:active { transform: scale(0.97) }` on chevrons/menu/new-group via a shared inline style or the existing global pattern if one exists (grep for `scale(0.9` in web/src first). All inside `@media (prefers-reduced-motion: reduce)` → no transform transitions (opacity only). Implement with a small `<style>` block or existing globals.css utilities — match whatever globals.css already does for hover/active states.
- [ ] **Step 8: Delete `SavedCard.tsx`, `CompareView.tsx`, `StageBoard.tsx`.** Grep first that nothing else imports them (recon says only within `components/saved/`, re-verify). Also grep `setStage(` imports — if the board was the last consumer, remove the export from `useSaved.ts`/`saved.ts` or leave if other surfaces (gem dossier?) use it. Follow the grep.
- [ ] **Step 9: Gates.** From `web/`: typecheck + lint + node --test, bare, full output read.
- [ ] **Step 10: Commit**

```bash
git add -A web/src/components/saved/ web/src/lib/
git commit -m "feat(saved): board-only My gems with user-defined group columns; overflow-proof cards; drop list+compare" -- web/src/components/saved/ web/src/lib/
```

---

### Task 6: Full gates + live e2e + copy pass

Run by the controller (main session), not a subagent, per verification rules.

- [ ] **Step 1:** Full test suites bare, solo: `backend` go test ./... ; `web` typecheck/lint/tests; python suite untouched but run the repo's standard gate if the migration touches shared fixtures. Zero failed, full output read, no pipes.
- [ ] **Step 2:** Live e2e against the local stack (real Postgres with migration 160 applied, real Go API, real web dev server; e2e login creds in memory `live-review-queue-shipped-2026-07-06.md`): in the browser — create a group, rename it, move a card into it via Move to, chevron it back, delete the group and watch cards land in the first column, reload the page and confirm persistence. Verify a long app name renders clamped at 2 lines with the score visible (seed one gem with a 60+ char name via SQL if none exists).
- [ ] **Step 3:** Humanizer pass on the new UI strings ("New group", "No gems here yet.", delete confirm, 409 detail "Keep at least one group.") — run the skill, fix, re-check against project plain-voice rules.
- [ ] **Step 4:** Whole-branch review (superpowers:requesting-code-review): composition questions — does every new endpoint have a live caller? does deleting the last board consumer of `setStage` leave a dead export? does `MySavedGems` still compile and the MCP shape hold?

### Task 7: Deploy

- [ ] Migration 160 to prod DB (backup gate first: `pg_dump` of `frontend.saved_gems` + empty `saved_groups` is trivial — dump saved_gems before the backfill UPDATE), then backend + web images per `DEPLOYMENT.md` / `web-api-gke-deploy-2026-06-30.md` memory (GKE ns agf; `docker buildx build --platform linux/amd64 --push`). Live-verify `/saved` on peon.works with the founder account. Update memory.
