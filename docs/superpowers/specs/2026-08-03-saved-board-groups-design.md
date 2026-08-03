# Saved board: board-only + custom groups — design

Date: 2026-08-03
Status: SUPERSEDED by v2 below (Edas 2026-08-03: "i needed only the grid of
items and then that i could create folders" — "board" meant the CARD GRID,
not the kanban view; kanban columns were the wrong reading)

## v5 — Spotify-style liking everywhere (Edas 2026-08-03: "pressing like
also opens dropdown of folders showing liked by default selected")

One unified folder-picker popover, and the heart becomes the entry point
product-wide (Discover table, gem dossier, Hunt cards — NOT the Review
deck, whose 👍/👎 triage stays untouched).

- **Heart press, unsaved gem:** optimistically save + record a like
  (`setRating 'like'`) + open the picker anchored at the heart. The heart
  fills instantly on press (respond on pointer-down feel).
- **Heart press, saved gem:** open the picker; no state change. Unsave
  moves INTO the picker: a plain "Remove from saved" row at the bottom
  (confirm not needed — it's recoverable by re-saving; it also clears
  memberships client-side consistent with server truth… memberships
  survive server-side (no FK); on re-save they resurface — acceptable,
  disclose) and closes the picker.
- **The picker (one shared component, replaces AddToFolderMenu):** first
  row "Liked" with the heart icon — checked ⟺ `app_ratings.rating='like'`;
  toggling writes like/none via the existing ratings endpoint with the SAME
  pending/settle/failure feedback as folder rows. Below: every user folder
  as today (checkbox membership rows, v4 feedback). Bottom: "Remove from
  saved" (only when saved; separated by the house divider). The /saved
  cards' "Add to folder" menu becomes this same picker (v4's "Liked
  excluded from menus" rule is superseded — Liked now appears first).
- **Store:** a liked-ids set derived from the v4 liked slice +
  `rateLike(advertiserId)` / `unrateLike(advertiserId)` optimistic actions
  wrapping `setRating`, updating the liked slice and pending set so Liked
  rows animate identically. Hunt's RoundBlock heart and GemNotes/GemTable
  SaveToggle all route through the unified control; RoundBlock's bespoke
  save+rate logic is replaced by it.
- **Copy:** rows "Liked", folder names, "Remove from saved". Plain voice.
- Out of scope: creating folders from inside the picker (offer later),
  Review deck changes, backend changes (none needed — ratings + membership
  endpoints exist).

## v4 — built-in Liked collection + clean slate + membership feedback
(Edas 2026-08-03: "remove the folders leave only liked ones and all the
other need to be customly created, move to folder should have some sort of
animation feedback"; Liked source confirmed via question = EVERYTHING liked)

- **Clean slate:** the 4 stage-named folders that migrations 160/162
  backfilled are deleted from prod (one-time data cleanup, backup-gated;
  memberships die by CASCADE, gems untouched). No code seeds folders —
  every real folder is user-created. (v2 no-seeding rule already covers
  new users.)
- **Liked (built-in):** a pinned, non-renamable, non-deletable collection
  shown FIRST in the folders row, heart icon instead of folder icon,
  auto-filled with every app the user has LIKED anywhere — Review 👍 and
  Hunt heart (`frontend.app_ratings.rating = 'like'`) — including apps
  never saved. Not a `saved_groups` row: sentinel route `?folder=liked`.
  - Go: `GET /api/liked-gems` (KeyAuth identity, no user_email param) →
    rows renderable as cards: `advertiser_id, page_name, app_name,
    icon_url, gem_score, rated_at, saved (bool)` — app_ratings filtered to
    like, joined to the same sources the shortlist/hunt rows use for
    name/icon/score (copy the hunt_repo LEFT JOIN idiom). Raw-key wire
    test.
  - Web: Liked tile count from a new liked slice (hydrated with groups).
    Liked view: saved members render as normal cards; liked-but-unsaved
    apps render the same card shape with the SaveToggle heart and NO
    folder menu / note / tags (those need a saved row). Liked is excluded
    from every "Add to folder" menu and from rename/delete affordances.
- **Membership toggle feedback (Add to folder menu):** instant press
  response; while the add/remove POST is in flight the toggled row shows a
  pending state (checkbox swaps to a small in-flight marker, row dimmed);
  on success the checkmark settles with a ~200ms critically-damped
  scale-in (0.9→1 + fade, ease-out, transform/opacity only); on failure
  the checkbox reverts, the row shakes briefly (2 cycles, ±3px, 250ms
  total), the menu STAYS OPEN for retry, and the row shows "Couldn't
  save." in small muted text until the next attempt. The trigger button
  pulses once (scale 1→1.04→1, 180ms) when a toggle confirms while the
  menu is closed... (only if trivially cheap — else drop). Store change:
  `addToFolder`/`removeFromFolder` return the persist Promise and expose a
  pending membership set the menu subscribes to; optimistic local state
  stays as-is (rollback via rehydrate on failure still applies).
  `prefers-reduced-motion`: no scale/shake — opacity crossfade + the
  error text only.
- Copy: Liked tile label "Liked". Failure text "Couldn't save." Plain
  voice throughout.

## v3 — real folders, Spotify-playlist model (Edas 2026-08-03: "i want
actual folders like playlists on spotify"; confirmed multi-membership)

Folders are navigable containers, not filters, and a gem can be in MANY
folders at once (playlist semantics).

- **Data (migration 162):** `frontend.saved_group_members (user_email TEXT
  NOT NULL, group_id BIGINT NOT NULL REFERENCES frontend.saved_groups(id)
  ON DELETE CASCADE, advertiser_id BIGINT NOT NULL, added_at TIMESTAMPTZ
  NOT NULL DEFAULT now(), PRIMARY KEY (group_id, advertiser_id))` + index
  `ix_saved_group_members_user (user_email, advertiser_id)`. Backfill:
  one membership row per existing `saved_gems.group_id`. The `group_id`
  column stays but is legacy (like `stage`): no longer read or written.
- **Go:** `ListSavedGems` rows gain `group_ids: [int64]` (aggregated,
  `[]` not null — raw-key wire test). New membership endpoints (KeyAuth,
  writes RequireToken, ownership → 404 like the rest):
  `POST /api/saved-groups/:id/gems {advertiser_id}` (idempotent add,
  ON CONFLICT DO NOTHING; membership rows carry the owner email) and
  `POST /api/saved-groups/:id/gems/remove {advertiser_id}` (idempotent).
  The upsert's `group_id` sentinel handling and `SavedGemIn/Row.GroupID`
  are REMOVED (web stops sending; columns stay in DB as legacy).
  Delete-folder semantics: memberships die with the folder (CASCADE);
  gems themselves are untouched. No seeding (v2 rule stands).
- **Web — two-level page.** Top level `/saved`: a "Folders" tile row/grid
  (one tile per folder: name + client-derived gem count; a "New folder"
  tile that creates + enters rename) above the full "All gems" card grid
  (unchanged card design). Opening a folder (tile click) shows the folder
  view: back control ("All gems"), folder name inline-renameable, ⋯ menu
  with Delete folder (confirm: `Delete "<name>"? Gems stay in your saved
  list.`), and the grid of member gems. Folder state rides a `?folder=<id>`
  URL param via history.replaceState (Next 16 router.replace no-op
  landmine). Unknown/foreign id in the param → top level.
- **Membership control on cards + GemNotes:** replace the single Folder
  select with an "Add to folder" control opening the house menu as a
  checkbox list (menuitemcheckbox per folder, toggling membership,
  keyboard nav reused). Zero folders → control hidden (create folders from
  the top level). SavedGem.groupId → `groupIds: number[]`; store gains
  `addToFolder(advertiserId, groupId)` / `removeFromFolder(advertiserId,
  groupId)` (optimistic, persisted to the new endpoints, rollback via
  rehydrate).
- **Carry-over cleanups from the v2 review:** delete dead
  moveGroup/moveGroupLeft/moveGroupRight exports; fix stale
  "column/board/bucketByGroup" comments; createGroup returns the temp id
  (no id set-difference inference); aria-pressed on folder tiles where
  applicable.
- **Copy:** "New folder", "Add to folder", "Delete folder", confirm above,
  folder-view empty state "No gems in this folder yet." All plain voice.

## v2 — card grid + folders (SUPERSEDED by v3: folders became filters,
Edas wanted containers)

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
