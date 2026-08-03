/**
 * saved-core.ts — pure types + helpers for the saved-gems store, with NO
 * dependency on the API/network layer so they unit-test under `node --test`.
 * The stateful, backend-backed store lives in saved.ts and re-exports these.
 */
import type { ShortlistRow } from "@/types/contracts";

export type Stage = "just_saw" | "watching" | "looks_real" | "building";
export type SavedVerdict = "build" | "clone" | "pass" | "revisit";
export type Confidence = 1 | 2 | 3 | 4 | 5;

/** Ordered stage pipeline (product-facing labels). */
export const STAGES: { id: Stage; label: string }[] = [
  { id: "just_saw", label: "Just saw" },
  { id: "watching", label: "Watching" },
  { id: "looks_real", label: "Looks real" },
  { id: "building", label: "Building" },
];

export const VERDICTS: { id: SavedVerdict; label: string }[] = [
  { id: "build", label: "Build" },
  { id: "clone", label: "Clone" },
  { id: "revisit", label: "Later" },
  { id: "pass", label: "Pass" },
];

export interface ScoreSnapshot {
  gemScore: number;
  scalingVelocity: number;
  underRadar: number;
  funnelIntent: number;
  activeAdCount: number;
  capturedAt: string; // ISO — when the gem was saved (why it was emerging THEN)
}

export interface SavedGem {
  advertiserId: number;
  pageName: string;
  appName: string | null;
  /** Store icon, denormalized at save time (persisted in frontend.saved_gems);
   * absent → the UI shows the initials tile. */
  iconUrl?: string;
  cohort: string;
  savedAt: string; // ISO
  scoreSnapshot: ScoreSnapshot;
  tags: string[];
  note: string;
  stage: Stage;
  verdict?: SavedVerdict;
  confidence?: Confidence;
  /** Board column the gem is filed under. `null` = never filed; the board shows
   * it in the first column (see `bucketByGroup`). */
  groupId: number | null;
}

/** One column of the /saved board (frontend.saved_groups). */
export interface SavedGroup {
  id: number;
  name: string;
  position: number;
}

export interface TagCount {
  name: string;
  uses: number;
}

/** Normalize a tag: lowercase, collapse non-alphanumerics to single spaces. */
export function normalizeTag(raw: string): string {
  return raw.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
}

export function makeSnapshot(row: ShortlistRow, capturedAt: string): ScoreSnapshot {
  return {
    gemScore: row.gem_score,
    scalingVelocity: row.scaling_velocity,
    underRadar: row.under_radar,
    funnelIntent: row.funnel_intent,
    activeAdCount: row.active_ad_count,
    capturedAt,
  };
}

export function makeSavedGem(row: ShortlistRow, savedAt: string): SavedGem {
  return {
    advertiserId: row.advertiser_id,
    pageName: row.page_name,
    appName: row.app_name,
    iconUrl: row.icon_url,
    cohort: row.cohort,
    savedAt,
    scoreSnapshot: makeSnapshot(row, savedAt),
    tags: [],
    note: "",
    stage: "just_saw",
    groupId: null,
  };
}

/** Outcome of merging an optimistic group with the row the backend created:
 *  the group to keep, and whether it still owes the server a write. */
export interface GroupReconcile {
  row: SavedGroup;
  needsPersist: boolean;
}

/**
 * Merge an optimistically-created column with the row the backend answered with.
 *
 * A create is a round-trip, and the board opens a brand-new column straight into
 * rename mode — so a rename (or a move) lands while the temp id is still in
 * flight. Those edits could not be persisted at the time (a negative id is not a
 * real row), so they exist ONLY here: taking the server row wholesale would
 * silently revert the user's typing on every single group creation.
 *
 * `submitted` is what `createGroup` sent. Any field the user has since changed
 * away from it wins over the server's value and is flagged for a follow-up
 * write; untouched fields take the server's value (it is authoritative — it may
 * have trimmed the name or picked a different position). `needsPersist` stays
 * false when the local value happens to match what the server already stored.
 */
export function reconcileGroupMerge(
  local: SavedGroup,
  submitted: SavedGroup,
  serverRow: SavedGroup,
): GroupReconcile {
  const renamed = local.name !== submitted.name;
  const moved = local.position !== submitted.position;
  const row: SavedGroup = {
    id: serverRow.id,
    name: renamed ? local.name : serverRow.name,
    position: moved ? local.position : serverRow.position,
  };
  const needsPersist = row.name !== serverRow.name || row.position !== serverRow.position;
  return { row, needsPersist };
}

/** Board order: by position, then id as a stable tiebreak. Returns a new array. */
export function sortGroups(groups: SavedGroup[]): SavedGroup[] {
  return [...groups].sort((a, b) => a.position - b.position || a.id - b.id);
}

/**
 * Split gems into one bucket per group, keyed by group id and ordered like the
 * board (`sortGroups`). A gem whose `groupId` is null — or points at a group the
 * user no longer has — lands in the FIRST column rather than disappearing.
 * Each bucket is sorted savedAt DESC (newest first), matching the list view.
 * With no groups there is nowhere to file anything, so the map comes back empty.
 */
export function bucketByGroup(
  gems: SavedGem[],
  groups: SavedGroup[],
): Map<number, SavedGem[]> {
  const buckets = new Map<number, SavedGem[]>();
  const ordered = sortGroups(groups);
  if (ordered.length === 0) return buckets;
  for (const g of ordered) buckets.set(g.id, []);

  const fallback = ordered[0].id;
  for (const gem of gems) {
    const key = gem.groupId != null && buckets.has(gem.groupId) ? gem.groupId : fallback;
    buckets.get(key)!.push(gem);
  }
  for (const list of buckets.values()) {
    list.sort((a, b) => b.savedAt.localeCompare(a.savedAt));
  }
  return buckets;
}

/** Immutably add a normalized tag (deduped). */
export function withTag(gem: SavedGem, raw: string): SavedGem {
  const tag = normalizeTag(raw);
  if (!tag || gem.tags.includes(tag)) return gem;
  return { ...gem, tags: [...gem.tags, tag] };
}

export function withoutTag(gem: SavedGem, tag: string): SavedGem {
  const t = normalizeTag(tag);
  return { ...gem, tags: gem.tags.filter((x) => x !== t) };
}

/** Build the tag registry (name → uses) from the saved map. */
export function rebuildTagRegistry(map: Record<string, SavedGem>): TagCount[] {
  const counts = new Map<string, number>();
  for (const gem of Object.values(map)) {
    for (const t of gem.tags) counts.set(t, (counts.get(t) ?? 0) + 1);
  }
  return [...counts.entries()]
    .map(([name, uses]) => ({ name, uses }))
    .sort((a, b) => b.uses - a.uses || a.name.localeCompare(b.name));
}

/** One optimistic-create reconciliation: the temp id the UI knew, the real id
 * the server minted, and a monotone sequence number so readers can apply each
 * entry exactly once with a cursor (append-only log — safe to re-read from a
 * render that React later discards). */
export interface GroupIdRemap {
  tempId: number;
  realId: number;
  seq: number;
}

/**
 * Apply the remap-log entries newer than `sinceSeq` to the board's UI
 * identity state. Attribution is exact — each entry names which temp id the
 * real id replaced — so multiple same-batch creates each land on their own
 * column (no array-order guessing). The real id inherits the temp column's
 * stable DOM key (chained if the temp already had one), and `editingId`
 * follows only its own temp id. Pure; returns fresh structures.
 */
export function applyGroupIdRemaps(
  log: readonly GroupIdRemap[],
  sinceSeq: number,
  stableKeys: ReadonlyMap<number, number>,
  editingId: number | null,
): { stableKeys: ReadonlyMap<number, number>; editingId: number | null; lastSeq: number } {
  let keys: Map<number, number> | null = null;
  let editing = editingId;
  let lastSeq = sinceSeq;
  for (const entry of log) {
    if (entry.seq <= sinceSeq) continue;
    keys = keys ?? new Map(stableKeys);
    keys.set(entry.realId, keys.get(entry.tempId) ?? entry.tempId);
    if (editing === entry.tempId) editing = entry.realId;
    if (entry.seq > lastSeq) lastSeq = entry.seq;
  }
  return { stableKeys: keys ?? stableKeys, editingId: editing, lastSeq };
}
