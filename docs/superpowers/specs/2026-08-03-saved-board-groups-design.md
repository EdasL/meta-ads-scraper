# Saved board: board-only + custom groups — design

Date: 2026-08-03
Status: SUPERSEDED by v2 below (Edas 2026-08-03: "i needed only the grid of
items and then that i could create folders" — "board" meant the CARD GRID,
not the kanban view; kanban columns were the wrong reading)

## v2 — card grid + folders (the correct reading)

The /saved page is ONE grid of gem cards (the old list-view grid,
`repeat(auto-fill, minmax(230px, 1fr))`) with user-created FOLDERS as a
filter dimension. No kanban columns.

- **Folder row** above the grid: chip "All · N" + one chip per folder
  ("Name · n"), active chip filled (--ink bg / --bg text). "New folder"
  control at the end, first-class size. Active folder filters the grid;
  All shows every gem. Tag filter stays and composes (AND).
- **Folder management** on the ACTIVE folder chip: inline rename (click
  name when active → input, Enter/blur commit, Esc cancel) and a ⋯ menu
  (reuses the shipped keyboard-nav menu) with Delete folder only.
  Delete confirm: `Delete "<name>"? Its gems stay in All.` — members are
  UNFILED (group_id → NULL), not reassigned. Deleting the last folder is
  allowed (folders are optional organization).
- **Cards**: resurrect the deleted SavedCard structure from git history
  (icon/initials tile, 2-line clamped name, score, tags, note clamp) MINUS
  the compare checkbox; add a compact folder `<select>` (selectStyle idiom,
  label "Folder") with options: each folder + "No folder". The overflow
  clamp rules from v1 stay.
- **Store/core**: groups slice unchanged (folders ARE groups). Rendering
  uses plain filtering (groupId === active folder; All = no filter); the
  bucketByGroup first-group FALLBACK rule is dead — NULL/unknown groupId
  is "unfiled", visible only under All. GemNotes picker gains "No folder"
  and drops the first-group display fallback; label "Folder".
- **Backend deltas** (folder semantics):
  1. `ListSavedGroups` no longer seeds defaults (new users start with zero
     folders; the advisory lock goes away with the seed). Existing users
     keep their migration-backfilled 4 folders and can delete them.
  2. `DeleteSavedGroup`: sets members' `group_id = NULL`, deletes the row;
     no last-group 409 (allowed down to zero).
  3. Upsert accepts explicit unfile: `group_id: 0` writes NULL (0 is never
     a real id); absent/null still means "keep server value" (COALESCE).
- **Copy**: user-facing noun is "folder" everywhere ("New folder",
  "No folder", "Delete folder", "No gems in this folder yet."). Internal
  names stay `saved_groups`/`group_id` — no schema churn.
- Everything else from v1 stands: migration 160 (harmless — backfilled
  folders are real folders), /api/saved-groups endpoints, remap-log
  reconcile, tombstone delete, proxy allowlist, stage column legacy.

## Goal

The `/saved` page becomes board-only. Cards stop overflowing their columns.
Users organize saved gems into their own groups, which are the board columns.
List view and compare view are removed.

## Interpretation call

"Add ability to create my own groups/folders" = user-defined board columns
replacing the fixed stage columns (`just_saw / watching / looks_real /
building`). Not a second folder dimension layered on top of stages. Existing
stage assignments seed each user's initial columns, so current boards look
identical after the migration.

## Current state (recon 2026-08-03)

- Route `web/src/app/saved/page.tsx` (10 lines) → `web/src/components/saved/SavedGems.tsx`
  (127) owning ViewSwitch (list/board/compare), tag filter, compare selection.
- `SavedCard.tsx` (91, list view) and `CompareView.tsx` (86) are imported only
  within `components/saved/` — deletable, no tests reference them.
- `StageBoard.tsx` (64): CSS grid `repeat(4, minmax(170px, 1fr))`,
  `overflowX: auto`; cards move via chevron buttons calling `setStage`.
- Overflow root cause (`StageBoard.tsx:41-47`): name `<Link>` in a flex row has
  no `minWidth: 0`, no clamp, no `wordBreak`; long names blow out the
  `minmax(170px, 1fr)` track. `SavedCard.tsx:34-53` has the correct pattern
  (flex:1, minWidth:0, -webkit-box 2-line clamp, break-word, reserved
  `minHeight: calc(2 * var(--lh-sm))`).
- Data: `frontend.saved_gems` (schema 102 + 114), PK `(user_email,
  advertiser_id)`, `stage` CHECK constraint, `tags` JSON text, `note`,
  `icon_url`. Store: `web/src/lib/saved.ts` (+ `saved-core.ts` pure /
  `useSaved.ts` hooks), optimistic mutate → `persist()`. API:
  `GET /saved-gems?user_email=…`, `POST /saved-gems/:advertiserId`,
  `POST /saved-gems/:advertiserId/remove` (`backend/controllers/saved_gems_controller.go`,
  `backend/repositories/saved_gems_repo.go`).
- Second reader of the table: `repositories.MySavedGems` (hosted MCP). Reads
  columns incl. `stage`; must keep compiling and returning data.
- No grouping concept exists beyond `tags` and `stage`. No dnd lib in
  package.json.

## Design

### Data (migration `db/schema/160_saved_groups.sql`)

```sql
CREATE TABLE frontend.saved_groups (
  id         BIGSERIAL PRIMARY KEY,
  user_email TEXT NOT NULL,
  name       TEXT NOT NULL,
  position   INT  NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_saved_groups_user ON frontend.saved_groups (user_email, position);

ALTER TABLE frontend.saved_gems ADD COLUMN group_id BIGINT
  REFERENCES frontend.saved_groups(id) ON DELETE SET NULL;
```

One-time backfill in the same migration: for every distinct `user_email` in
`saved_gems`, insert 4 groups named after the stage labels ("Just saw",
"Watching", "Looks real", "Building"; positions 0-3) and set each row's
`group_id` from its `stage`. Idempotent guard: skip users who already have
rows in `saved_groups`.

`stage` column: kept, no longer written by the board. Legacy for MCP reads.
`group_id IS NULL` (post-migration saves, deleted-group fallout) renders in
the user's first column and is claimed by the next explicit move.

New users / empty boards: the groups list endpoint seeds the 4 default groups
for a user who has none (single INSERT … SELECT WHERE NOT EXISTS, race-safe).
Seeding lives in the repo function, not the client.

### API (Go, modern hunt idiom: identity via KeyAuth `UserFrom`, never a query param)

- `GET  /api/saved-groups` → `[{id, name, position}]` (seeds defaults if none)
- `POST /api/saved-groups` `{name}` → created group (position = max+1)
- `POST /api/saved-groups/:id/update` `{name?, position?}` → 404 on ownership miss
- `POST /api/saved-groups/:id/remove` → refuses (409) to delete the user's
  only group; otherwise reassigns the group's gems to the user's first
  remaining group, then deletes.

Gem→group assignment: extend the existing `POST /saved-gems/:advertiserId`
upsert with optional `group_id` (validated: must belong to the same user,
else 400). `SavedGemRow` gains `GroupID *int64`. TS mirrors updated in
`web/src/types/api.ts`.

Reorder = client sends one `update {position}` per moved group (max a handful
of columns; no bulk endpoint needed).

### Web

- Delete `SavedCard.tsx`, `CompareView.tsx`; strip ViewSwitch + compare
  selection from `SavedGems.tsx`. Tag filter stays. `useSavedList` export
  surface unchanged (AppHeader + ActivationChecklist depend on it).
- Store: `saved.ts` gains `groups` state (hydrate from `/saved-groups`,
  optimistic create/rename/delete/reorder + `persist`), `setGroup(advertiserId,
  groupId)` replacing `setStage` usage on the board. Pure helpers (column
  bucketing incl. NULL→first-column rule, position sort, reassign-on-delete)
  live in `saved-core.ts` with node --test coverage.
- `StageBoard.tsx` → `GroupBoard.tsx`: columns from `groups` ordered by
  `position`, grid tracks `minmax(200px, 280px)`, board scrolls horizontally.
- Card overflow fix: name link gets `flex: 1, minWidth: 0`, 2-line clamp with
  `minHeight: calc(2 * var(--lh-sm))`, `wordBreak: "break-word"`; score span
  `flexShrink: 0`. Same treatment for the page-name subline if present.
- Column header: name is inline-editable (click to rename, Enter/blur commits,
  Esc cancels); count badge; overflow menu (⋯) with Move left / Move right /
  Delete group. Delete confirms and states where cards go. "New group" is a
  first-class control at the end of the board sized like existing primary
  buttons (per product defaults), creates then focuses the name for rename.
- Card movement: keep ‹ › chevrons (adjacent column) + a "Move to…" item
  listing columns when there are >2 groups (small menu, same idiom as the
  column menu).
- Motion: 150-200ms ease-out transforms on card move/menu open; no layout
  property animation; respects prefers-reduced-motion. No load choreography.
- Copy (plain voice, humanizer pass at draft time): "New group", "Rename",
  "Delete group", confirm body "Cards move to <first group name>.", empty
  column state "No gems here yet.", empty board unchanged from current empty
  state.

### Error handling

- Optimistic updates roll back on persist failure (existing saved.ts pattern —
  keep whatever rollback semantics it has; if none, failed persist re-hydrates).
- Deleting a group that the server refuses (last group, 409) surfaces the
  API error message inline near the menu; board state unchanged.
- Concurrent edits from another tab resolve on next hydrate; no realtime sync.

### Testing

- `saved-core.ts` new pure helpers: node --test (bucketing, NULL fallback,
  position math, delete-reassign).
- Go: integration test on saved_groups repo (seed-on-empty, ownership 404,
  delete-reassign, last-group 409) following `member_repo_test.go` DB idiom;
  raw-key JSON shape test for new wire fields (cross-fence rule).
- Backfill: migration applied against the integration DB; assert stage→group
  mapping for a seeded user.
- Live e2e gate (2+ components: DB + Go + web): against the local stack,
  create a group, rename it, move a card into it, delete it, verify cards
  land in the first column — through the real API, verified rendered in the
  browser.
- MCP `MySavedGems` still compiles and returns rows (it reads `stage`; column
  remains).

### Out of scope

Drag-and-drop, nested folders, group sharing, per-group colors/icons,
removing the `stage` column, changing the saved-gems auth idiom.
