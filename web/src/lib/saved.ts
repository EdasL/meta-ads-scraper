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
  createSavedGroup,
  getSavedGems,
  getSavedGroups,
  removeSavedGem as apiRemove,
  removeSavedGroup,
  updateSavedGroup,
  upsertSavedGem,
} from "./api";
import {
  makeSavedGem,
  rebuildTagRegistry,
  sortGroups,
  withTag,
  withoutTag,
  type Confidence,
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
    groupId: r.group_id ?? null,
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
    // Always the gem's CURRENT group. The backend applies this with COALESCE,
    // so null means "keep the stored value", not "clear it" — sending null for a
    // gem that has a group would silently leave it where it was. Send the
    // concrete id whenever we have one and the column always follows the gem.
    group_id: gem.groupId,
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

/** Board columns, always kept in board order. Replaced (never mutated) so the
 *  groups snapshot identity only changes when the board actually changes. */
let groups: SavedGroup[] = [];
let groupsHydratedKey: string | null = null;

/** Optimistically-created groups get a negative id until the backend answers
 *  with the real one. Nothing negative is ever sent to the API. */
let nextTempId = -1;
const isTemp = (id: number) => id < 0;

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
  // Force a re-hydrate of both halves to resync the optimistic state: a failed
  // group write can leave gems pointing at a column that never existed.
  hydratedKey = null;
  groupsHydratedKey = null;
  void hydrate();
}

/** Persist a single change to the backend. */
function persist(changed: SavedGem | null, removedId?: number) {
  if (!userEmail) return;
  if (removedId != null) apiRemove(removedId, userEmail).catch(onSyncError);
  else if (changed) upsertSavedGem(changed.advertiserId, toUpsert(changed, userEmail)).catch(onSyncError);
}

/** Fetch the board columns. Tracked by its own key so a groups failure never
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

async function hydrate() {
  void hydrateGroups();
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
  cache = {};
  groups = [];
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
export function setStage(id: number, stage: Stage): void {
  mutate(id, (g) => ({ ...g, stage }));
}
export function setVerdict(id: number, verdict: SavedVerdict | undefined): void {
  mutate(id, (g) => ({ ...g, verdict }));
}
export function setConfidence(id: number, confidence: Confidence | undefined): void {
  mutate(id, (g) => ({ ...g, confidence }));
}

// ---------------------------------------------------------------------------
// Board groups (columns of /saved)
// ---------------------------------------------------------------------------

function setGroups(next: SavedGroup[]) {
  groups = sortGroups(next);
  notify();
}

/** Persist one column patch. Temp ids have no backend row yet, so they are
 *  skipped — the create response is the source of truth for those. */
function persistGroup(id: number, patch: ApiSavedGroupPatch) {
  if (!userEmail || isTemp(id)) return;
  updateSavedGroup(id, patch).catch(onSyncError);
}

/** Swap the optimistic temp group for the real backend row and move any gems
 *  that were dropped into it while the create was in flight. */
function reconcileGroup(tempId: number, row: SavedGroup) {
  setGroups(groups.map((g) => (g.id === tempId ? row : g)));
  const map = ensureCache();
  const moved: SavedGem[] = [];
  const next: SavedMap = { ...map };
  for (const gem of Object.values(map)) {
    if (gem.groupId !== tempId) continue;
    const updated = { ...gem, groupId: row.id };
    next[gem.advertiserId] = updated;
    moved.push(updated);
  }
  if (moved.length === 0) return;
  cache = next;
  recompute();
  notify();
  for (const gem of moved) persist(gem);
}

export function listGroups(): SavedGroup[] {
  return groups;
}

export function createGroup(name: string): void {
  const trimmed = name.trim();
  if (!trimmed) return;
  const tempId = nextTempId--;
  const position = groups.reduce((max, g) => Math.max(max, g.position), -1) + 1;
  setGroups([...groups, { id: tempId, name: trimmed, position }]);
  if (!userEmail) return;
  createSavedGroup(trimmed)
    .then((row) => reconcileGroup(tempId, { id: row.id, name: row.name, position: row.position }))
    .catch(onSyncError);
}

export function renameGroup(id: number, name: string): void {
  const trimmed = name.trim();
  if (!trimmed) return;
  const current = groups.find((g) => g.id === id);
  if (!current || current.name === trimmed) return;
  setGroups(groups.map((g) => (g.id === id ? { ...g, name: trimmed } : g)));
  persistGroup(id, { name: trimmed });
}

/** Move a column one slot along the board. Positions are renumbered to plain
 *  0..n-1 so ties and the gaps a delete leaves behind cannot stall the swap. */
function moveGroup(id: number, delta: -1 | 1) {
  const ordered = sortGroups(groups);
  const i = ordered.findIndex((g) => g.id === id);
  if (i < 0) return;
  const j = i + delta;
  if (j < 0 || j >= ordered.length) return;
  const swapped = [...ordered];
  [swapped[i], swapped[j]] = [swapped[j], swapped[i]];
  const next = swapped.map((g, idx) => ({ ...g, position: idx }));
  setGroups(next);
  for (const g of next) {
    const before = ordered.find((o) => o.id === g.id);
    if (before && before.position !== g.position) persistGroup(g.id, { position: g.position });
  }
}

export function moveGroupLeft(id: number): void {
  moveGroup(id, -1);
}

export function moveGroupRight(id: number): void {
  moveGroup(id, 1);
}

export function deleteGroup(id: number): void {
  if (groups.length <= 1) return; // the board keeps at least one column (backend 409s)
  const remaining = sortGroups(groups.filter((g) => g.id !== id));
  if (remaining.length === groups.length) return; // unknown id
  // Refile the column's gems locally; the backend does the same server-side.
  const target = remaining[0].id;
  const map = ensureCache();
  const next: SavedMap = { ...map };
  for (const gem of Object.values(map)) {
    if (gem.groupId === id) next[gem.advertiserId] = { ...gem, groupId: target };
  }
  cache = next;
  recompute();
  setGroups(remaining);
  if (!userEmail || isTemp(id)) return; // a temp column was never persisted
  removeSavedGroup(id).catch(onSyncError);
}

/** File a saved gem under a column. Rides the saved-gem upsert. */
export function setGroup(advertiserId: number, groupId: number): void {
  const map = ensureCache();
  const gem = map[advertiserId];
  if (!gem || gem.groupId === groupId) return;
  const updated: SavedGem = { ...gem, groupId };
  cache = { ...map, [advertiserId]: updated };
  recompute();
  notify();
  // A temp column is not a real row yet and the backend would reject it (400);
  // reconcileGroup() persists these gems once the create response lands.
  if (!isTemp(groupId)) persist(updated);
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
