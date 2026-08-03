/**
 * saved.ts — the "my gems" store. The source of truth is the Go backend
 * (frontend.saved_gems): the store hydrates from GET /saved-gems and every
 * mutation is persisted via POST /saved-gems (optimistic UI + background write).
 *
 * Pure types + helpers live in ./saved-core (unit-tested, no network); this
 * module adds the stateful, backend-backed store and re-exports the core.
 */
import type { ShortlistRow } from "@/types/contracts";
import type { ApiSavedGem, ApiSavedGemIn, ApiSavedGroupPatch } from "@/types/api";
import {
  addSavedGroupGem,
  createSavedGroup,
  getLikedGems,
  getSavedGems,
  getSavedGroups,
  removeSavedGem as apiRemove,
  removeSavedGroup,
  removeSavedGroupGem,
  setRating,
  updateSavedGroup,
  upsertSavedGem,
} from "./api";
import {
  makeSavedGem,
  membershipKey,
  rebuildTagRegistry,
  reconcileGroupMerge,
  sortGroups,
  withFolderId,
  withoutFolderId,
  withTag,
  withoutTag,
  type Confidence,
  type GroupIdRemap,
  type SavedGem,
  type SavedGroup,
  type SavedVerdict,
  type Stage,
  type TagCount,
} from "./saved-core";

export * from "./saved-core";

// ---------------------------------------------------------------------------
// API <-> store mapping
// ---------------------------------------------------------------------------

function fromApi(r: ApiSavedGem): SavedGem {
  const savedAt = r.saved_at ?? new Date().toISOString();
  return {
    advertiserId: r.advertiser_id,
    pageName: r.page_name ?? "",
    appName: r.app_name,
    iconUrl: r.icon_url ?? undefined,
    cohort: r.cohort,
    savedAt,
    scoreSnapshot: {
      gemScore: r.gem_score,
      scalingVelocity: r.scaling_velocity ?? 0,
      underRadar: r.under_radar ?? 0,
      funnelIntent: r.funnel_intent ?? 0,
      activeAdCount: r.active_ad_count ?? 0,
      capturedAt: savedAt,
    },
    tags: r.tags ?? [],
    note: r.note ?? "",
    stage: (r.stage as Stage) ?? "just_saw",
    verdict: (r.verdict as SavedVerdict) ?? undefined,
    confidence: (r.confidence as Confidence) ?? undefined,
    groupIds: r.group_ids ?? [],
  };
}

function toUpsert(gem: SavedGem, userEmail: string): ApiSavedGemIn {
  const s = gem.scoreSnapshot;
  return {
    user_email: userEmail,
    page_name: gem.pageName || null,
    app_name: gem.appName,
    icon_url: gem.iconUrl ?? null,
    cohort: gem.cohort,
    gem_score: s.gemScore,
    scaling_velocity: s.scalingVelocity,
    under_radar: s.underRadar,
    funnel_intent: s.funnelIntent,
    active_ad_count: s.activeAdCount,
    tags: gem.tags,
    note: gem.note,
    stage: gem.stage,
    verdict: gem.verdict ?? null,
    confidence: gem.confidence ?? null,
    // Folder memberships live in frontend.saved_group_members and travel via
    // the /saved-groups/:id/gems endpoints — never through this upsert.
  };
}

// ---------------------------------------------------------------------------
// Store (in-memory cache; persistence = backend)
// ---------------------------------------------------------------------------

type SavedMap = Record<number, SavedGem>;

let cache: SavedMap | null = null;
let cachedList: SavedGem[] = [];
const listeners = new Set<() => void>();
let userEmail: string | null = null;
let hydratedKey: string | null = null;

/** Folders, always kept in display order. Replaced (never mutated) so the
 *  groups snapshot identity only changes when the folder set actually changes. */
let groups: SavedGroup[] = [];
let groupsHydratedKey: string | null = null;

/** Built-in Liked collection: every app the user liked anywhere (Review 👍,
 * Hunt heart), saved or not. Read-only client-side; hydrated with the rest. */
export interface LikedGem {
  advertiserId: number;
  pageName: string;
  appName: string | null;
  iconUrl?: string;
  gemScore: number;
  ratedAt: string;
  saved: boolean;
}

let liked: LikedGem[] = [];
let likedHydratedKey: string | null = null;

/** Optimistically-created groups get a negative id until the backend answers
 *  with the real one. Nothing negative is ever sent to the API. */
let nextTempId = -1;
const isTemp = (id: number) => id < 0;

/**
 * Temp ids the user explicitly deleted while their create was still in flight.
 *
 * This set is the ONLY signal that a pending folder should be deleted
 * server-side. A temp id merely being ABSENT from `groups` does not mean the
 * user removed it: `hydrateGroups` replaces `groups` wholesale, and it fires on
 * any unrelated sync failure (`onSyncError`) or a user switch — server data
 * never contains negative ids, so any such hydrate wipes the pending folder too.
 * Treating that absence as a delete would destroy a group the backend had just
 * successfully created. Absence without a tombstone is a no-op; the real folder
 * shows up on the next hydrate by itself.
 */
const deletedTempIds = new Set<number>();

/** Append-only log of temp→real id swaps from create reconciliation, so the
 * page can re-attribute its DOM keys and editing session to the exact folder
 * that reconciled (never by array-order inference). Readers keep a seq cursor
 * (`applyGroupIdRemaps`) instead of draining: a React render that gets
 * discarded (StrictMode) re-reads the same entries harmlessly. Bounded per
 * session — one entry per optimistic create; cleared on user switch. */
let groupIdRemapLog: readonly GroupIdRemap[] = [];
let groupIdRemapSeq = 0;

export function getGroupIdRemapLog(): readonly GroupIdRemap[] {
  return groupIdRemapLog;
}

function recompute() {
  cachedList = Object.values(cache ?? {}).sort((a, b) => b.savedAt.localeCompare(a.savedAt));
}

function notify() {
  listeners.forEach((l) => l());
}

/** Lazily seed the in-memory cache (empty; hydrate() fills it from the backend). */
function ensureCache(): SavedMap {
  if (cache) return cache;
  cache = {};
  recompute();
  return cache;
}

function onSyncError(e: unknown) {
  console.error("saved: backend sync failed", e);
  // Force a re-hydrate of every slice to resync the optimistic state: a failed
  // group write can leave gems pointing at a folder that never existed.
  hydratedKey = null;
  groupsHydratedKey = null;
  likedHydratedKey = null;
  void hydrate();
}

/** Persist a single change to the backend. */
function persist(changed: SavedGem | null, removedId?: number) {
  if (!userEmail) return;
  if (removedId != null) apiRemove(removedId, userEmail).catch(onSyncError);
  else if (changed) upsertSavedGem(changed.advertiserId, toUpsert(changed, userEmail)).catch(onSyncError);
}

/** Fetch the folders. Tracked by its own key so a groups failure never
 *  blanks the gems (and vice versa). */
async function hydrateGroups() {
  const key = userEmail ?? "";
  if (groupsHydratedKey === key) return;
  groupsHydratedKey = key;
  if (!userEmail) return; // wait until the session user is known
  try {
    const rows = await getSavedGroups();
    groups = sortGroups(rows.map((r) => ({ id: r.id, name: r.name, position: r.position })));
    notify();
  } catch (e) {
    groupsHydratedKey = null; // allow retry on next subscribe
    console.error("saved: group hydrate failed", e);
  }
}

async function hydrateLiked() {
  const key = userEmail ?? "";
  if (likedHydratedKey === key) return;
  likedHydratedKey = key;
  if (!userEmail) return; // wait until the session user is known
  try {
    const rows = await getLikedGems();
    liked = rows.map((r) => ({
      advertiserId: r.advertiser_id,
      pageName: r.page_name ?? "",
      appName: r.app_name,
      iconUrl: r.icon_url ?? undefined,
      gemScore: r.gem_score,
      ratedAt: r.rated_at,
      saved: r.saved,
    }));
    notify();
  } catch (e) {
    likedHydratedKey = null; // allow retry on next subscribe
    console.error("saved: liked hydrate failed", e);
  }
}

async function hydrate() {
  void hydrateGroups();
  void hydrateLiked();
  const key = userEmail ?? "";
  if (hydratedKey === key) return;
  hydratedKey = key;
  if (!userEmail) return; // wait until the session user is known
  try {
    const rows = await getSavedGems(userEmail);
    const map: SavedMap = {};
    for (const r of rows) map[r.advertiser_id] = fromApi(r);
    cache = map;
    recompute();
    notify();
  } catch (e) {
    hydratedKey = null; // allow retry on next subscribe
    console.error("saved: hydrate failed", e);
  }
}

/** Set the current user (saved gems are keyed by email) and refetch. */
export function setUser(email: string | null) {
  if (userEmail === email) return;
  userEmail = email;
  hydratedKey = null;
  groupsHydratedKey = null;
  likedHydratedKey = null;
  cache = {};
  groups = [];
  liked = [];
  deletedTempIds.clear(); // never carry one user's pending deletes into another's folders
  groupIdRemapLog = []; // stale temp→real mappings mean nothing on another user's folders
  recompute();
  notify();
  void hydrate();
}

function mutate(id: number, fn: (gem: SavedGem) => SavedGem) {
  const map = ensureCache();
  const gem = map[id];
  if (!gem) return;
  const updated = fn(gem);
  cache = { ...map, [id]: updated };
  recompute();
  notify();
  persist(updated);
}

// --- queries ---
export function listSaved(): SavedGem[] {
  ensureCache();
  return cachedList;
}
export function getSaved(id: number): SavedGem | undefined {
  return ensureCache()[id];
}
export function isSaved(id: number): boolean {
  return !!ensureCache()[id];
}
export function allTags(): TagCount[] {
  return rebuildTagRegistry(ensureCache());
}

// --- mutations (optimistic; persisted in the background) ---
export function save(row: ShortlistRow): void {
  const map = ensureCache();
  if (map[row.advertiser_id]) return; // keep existing snapshot/notes
  const gem = makeSavedGem(row, new Date().toISOString());
  cache = { ...map, [row.advertiser_id]: gem };
  recompute();
  notify();
  persist(gem);
}
export function unsave(id: number): void {
  const map = ensureCache();
  if (!map[id]) return;
  const next = { ...map };
  delete next[id];
  cache = next;
  recompute();
  notify();
  persist(null, id);
}
export function toggleSave(row: ShortlistRow): void {
  if (isSaved(row.advertiser_id)) unsave(row.advertiser_id);
  else save(row);
}
export function setNote(id: number, note: string): void {
  mutate(id, (g) => ({ ...g, note }));
}
export function addTag(id: number, raw: string): void {
  mutate(id, (g) => withTag(g, raw));
}
export function removeTag(id: number, tag: string): void {
  mutate(id, (g) => withoutTag(g, tag));
}
export function setVerdict(id: number, verdict: SavedVerdict | undefined): void {
  mutate(id, (g) => ({ ...g, verdict }));
}
export function setConfidence(id: number, confidence: Confidence | undefined): void {
  mutate(id, (g) => ({ ...g, confidence }));
}

// ---------------------------------------------------------------------------
// Folders (frontend.saved_groups) + memberships (saved_group_members)
// ---------------------------------------------------------------------------

function setGroups(next: SavedGroup[]) {
  groups = sortGroups(next);
  notify();
}

/** Persist one folder patch. Temp ids have no backend row yet, so they are
 *  skipped — the create response is the source of truth for those. */
function persistGroup(id: number, patch: ApiSavedGroupPatch) {
  if (!userEmail || isTemp(id)) return;
  updateSavedGroup(id, patch).catch(onSyncError);
}

/**
 * Swap the optimistic temp folder for the real backend row, keeping anything
 * the user changed during the round-trip (see `reconcileGroupMerge`) and
 * re-homing any memberships added while the create was in flight.
 */
function reconcileGroup(submitted: SavedGroup, serverRow: SavedGroup) {
  const tempId = submitted.id;
  if (deletedTempIds.delete(tempId)) {
    // The user deleted the folder mid-flight. Drop the row the backend just made
    // so it cannot reappear on the next hydrate.
    removeSavedGroup(serverRow.id).catch(onSyncError);
    return;
  }
  const local = groups.find((g) => g.id === tempId);
  // Absent WITHOUT a tombstone means a hydrate landed mid-flight and wiped the
  // pending folder (see deletedTempIds). Nothing to merge and nothing to delete:
  // the real row is already on the server and arrives with the next hydrate.
  if (!local) return;

  const { row, needsPersist } = reconcileGroupMerge(local, submitted, serverRow);
  // Exact temp→real attribution for the page's DOM/editing identity. Append
  // BEFORE the notify inside setGroups, so any render triggered by the swap
  // already sees its log entry. (No entry on the tombstone/hydrate-wipe paths:
  // there is no live folder for the UI to re-attribute.)
  groupIdRemapLog = [...groupIdRemapLog, { tempId, realId: serverRow.id, seq: ++groupIdRemapSeq }];
  setGroups(groups.map((g) => (g.id === tempId ? row : g)));
  // A rename made against the temp id could not be sent at the time — now
  // that the folder has a real id, it finally reaches the server.
  if (needsPersist) persistGroup(row.id, { name: row.name, position: row.position });

  // Memberships added while the create was in flight point at the temp id:
  // re-home them locally and persist each against the real folder.
  const map = ensureCache();
  const moved: SavedGem[] = [];
  const next: SavedMap = { ...map };
  for (const gem of Object.values(map)) {
    if (!gem.groupIds.includes(tempId)) continue;
    const updated = withFolderId(withoutFolderId(gem, tempId), row.id);
    next[gem.advertiserId] = updated;
    moved.push(updated);
  }
  if (moved.length === 0) return;
  cache = next;
  recompute();
  notify();
  for (const gem of moved) addSavedGroupGem(row.id, gem.advertiserId).catch(onSyncError);
}

export function listGroups(): SavedGroup[] {
  return groups;
}

/** Create a folder optimistically; returns its TEMP id (negative) so the UI
 * can activate/rename it directly, or null when the name was empty. The remap
 * log carries the temp→real swap once the backend answers. */
export function createGroup(name: string): number | null {
  const trimmed = name.trim();
  if (!trimmed) return null;
  const tempId = nextTempId--;
  const position = groups.reduce((max, g) => Math.max(max, g.position), -1) + 1;
  // Kept verbatim: reconciliation diffs the live folder against what was sent
  // to tell a mid-flight user edit apart from an untouched field.
  const submitted: SavedGroup = { id: tempId, name: trimmed, position };
  setGroups([...groups, submitted]);
  if (!userEmail) return tempId;
  createSavedGroup(trimmed)
    .then((row) =>
      reconcileGroup(submitted, { id: row.id, name: row.name, position: row.position }),
    )
    .catch(onSyncError);
  return tempId;
}

export function renameGroup(id: number, name: string): void {
  const trimmed = name.trim();
  if (!trimmed) return;
  const current = groups.find((g) => g.id === id);
  if (!current || current.name === trimmed) return;
  setGroups(groups.map((g) => (g.id === id ? { ...g, name: trimmed } : g)));
  persistGroup(id, { name: trimmed });
}

export function deleteGroup(id: number): void {
  const remaining = sortGroups(groups.filter((g) => g.id !== id));
  if (remaining.length === groups.length) return; // unknown id
  // Strip the folder from every gem's memberships locally; server-side the
  // membership rows die with the folder (CASCADE). Gems themselves stay.
  const map = ensureCache();
  const next: SavedMap = { ...map };
  for (const gem of Object.values(map)) {
    if (gem.groupIds.includes(id)) next[gem.advertiserId] = withoutFolderId(gem, id);
  }
  cache = next;
  recompute();
  setGroups(remaining);
  if (isTemp(id)) {
    // No backend row to delete yet — the create is still in flight. Tombstone it
    // so reconcileGroup removes the row the moment the create answers.
    deletedTempIds.add(id);
    return;
  }
  if (!userEmail) return;
  removeSavedGroup(id).catch(onSyncError);
}

/** Membership writes currently in flight (`membershipKey` entries). Replaced
 * immutably so it can be a useSyncExternalStore snapshot; the menu renders
 * pending rows from it. */
let pendingMemberships: ReadonlySet<string> = new Set();

export function getPendingMemberships(): ReadonlySet<string> {
  return pendingMemberships;
}

function markMembershipPending(key: string, pending: boolean) {
  const next = new Set(pendingMemberships);
  if (pending) next.add(key);
  else next.delete(key);
  pendingMemberships = next;
  notify();
}

/** Run one membership write with pending-set bookkeeping. The returned promise
 * REJECTS on failure so the menu can show its error state; the store-level
 * rollback (onSyncError rehydrate) is attached here, and so is a no-op catch —
 * callers that ignore the promise never cause an unhandled rejection. */
function persistMembership(key: string, write: () => Promise<void>): Promise<void> {
  markMembershipPending(key, true);
  const p = write()
    .catch((e: unknown) => {
      onSyncError(e);
      throw e;
    })
    .finally(() => markMembershipPending(key, false));
  p.catch(() => {});
  return p;
}

/** Sentinel "folder" id for the built-in Liked row in the picker's pending
 * set. 0 is never a real saved_groups id. */
const LIKED_PENDING_ID = 0;

export function likedPendingKey(advertiserId: number): string {
  return membershipKey(advertiserId, LIKED_PENDING_ID);
}

export function isLiked(advertiserId: number): boolean {
  return liked.some((l) => l.advertiserId === advertiserId);
}

/** What the liked slice needs to render an optimistic entry before the
 * backend echoes it — identity + display fields from the row at hand. */
export interface LikeSeed {
  pageName: string;
  appName: string | null;
  iconUrl?: string;
  gemScore: number;
}

/** Record a like (Spotify-style heart). Optimistic: the liked slice gains the
 * entry immediately; the write rides the same pending/settle/failure cycle as
 * folder memberships (rejects on failure, rolls back via rehydrate). */
export function rateLike(advertiserId: number, seed: LikeSeed): Promise<void> {
  if (isLiked(advertiserId)) return Promise.resolve();
  liked = [
    {
      advertiserId,
      pageName: seed.pageName,
      appName: seed.appName,
      iconUrl: seed.iconUrl,
      gemScore: seed.gemScore,
      ratedAt: new Date().toISOString(),
      saved: isSaved(advertiserId),
    },
    ...liked,
  ];
  notify();
  if (!userEmail) return Promise.resolve();
  const email = userEmail;
  return persistMembership(likedPendingKey(advertiserId), () =>
    setRating(advertiserId, email, "like", ""),
  );
}

/** Clear a like (rating → none). Same optimistic + pending semantics. */
export function unrateLike(advertiserId: number): Promise<void> {
  if (!isLiked(advertiserId)) return Promise.resolve();
  liked = liked.filter((l) => l.advertiserId !== advertiserId);
  notify();
  if (!userEmail) return Promise.resolve();
  const email = userEmail;
  return persistMembership(likedPendingKey(advertiserId), () =>
    setRating(advertiserId, email, "none", ""),
  );
}

/** Add a gem to a folder (playlist model — idempotent, additive). For a temp
 * folder the persist waits for reconcileGroup, which re-homes the membership
 * onto the real id the moment the create answers. Resolves when the write
 * lands; rejects on failure (local state rolls back via rehydrate). */
export function addToFolder(advertiserId: number, folderId: number): Promise<void> {
  const map = ensureCache();
  const gem = map[advertiserId];
  if (!gem || gem.groupIds.includes(folderId)) return Promise.resolve();
  cache = { ...map, [advertiserId]: withFolderId(gem, folderId) };
  recompute();
  notify();
  if (!userEmail || isTemp(folderId)) return Promise.resolve();
  return persistMembership(membershipKey(advertiserId, folderId), () =>
    addSavedGroupGem(folderId, advertiserId),
  );
}

/** Remove a gem from a folder. A temp folder has no backend rows yet — the
 * local removal is enough (reconcileGroup only re-homes memberships that are
 * still present). */
export function removeFromFolder(advertiserId: number, folderId: number): Promise<void> {
  const map = ensureCache();
  const gem = map[advertiserId];
  if (!gem || !gem.groupIds.includes(folderId)) return Promise.resolve();
  cache = { ...map, [advertiserId]: withoutFolderId(gem, folderId) };
  recompute();
  notify();
  if (!userEmail || isTemp(folderId)) return Promise.resolve();
  return persistMembership(membershipKey(advertiserId, folderId), () =>
    removeSavedGroupGem(folderId, advertiserId),
  );
}

// --- external store (for useSyncExternalStore) ---
export function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  ensureCache();
  void hydrate();
  return () => {
    listeners.delete(listener);
  };
}
export function getSnapshot(): SavedGem[] {
  ensureCache();
  return cachedList;
}
// Must be a stable reference — useSyncExternalStore loops if this changes.
const EMPTY: SavedGem[] = [];
export function getServerSnapshot(): SavedGem[] {
  return EMPTY;
}

// Groups ride the same listener set but get their own snapshot, so
// useSavedList's shape stays exactly what AppHeader/ActivationChecklist expect.
// A gem-only change leaves the groups array identity untouched, so the groups
// hook bails out of re-rendering.
export function subscribeGroups(listener: () => void): () => void {
  listeners.add(listener);
  ensureCache();
  void hydrate();
  return () => {
    listeners.delete(listener);
  };
}
export function getGroupsSnapshot(): SavedGroup[] {
  return groups;
}
const EMPTY_GROUPS: SavedGroup[] = [];
export function getGroupsServerSnapshot(): SavedGroup[] {
  return EMPTY_GROUPS;
}

// The Liked collection rides the same listener set with its own snapshot.
export function subscribeLiked(listener: () => void): () => void {
  listeners.add(listener);
  ensureCache();
  void hydrate();
  return () => {
    listeners.delete(listener);
  };
}
export function getLikedSnapshot(): LikedGem[] {
  return liked;
}
const EMPTY_LIKED: LikedGem[] = [];
export function getLikedServerSnapshot(): LikedGem[] {
  return EMPTY_LIKED;
}
