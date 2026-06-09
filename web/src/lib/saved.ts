/**
 * saved.ts — the "my gems" store. In LIVE mode the source of truth is the Go
 * backend (frontend.saved_gems): the store hydrates from GET /saved-gems and
 * every mutation is persisted via POST /saved-gems (optimistic UI + background
 * write). In MOCK mode it falls back to localStorage so the app runs offline
 * for dev — the same mock/live split every other endpoint uses.
 *
 * Pure types + helpers live in ./saved-core (unit-tested, no network); this
 * module adds the stateful, backend-backed store and re-exports the core.
 */
import type { ShortlistRow } from "@/types/contracts";
import type { ApiSavedGem, ApiSavedGemIn } from "@/types/api";
import { DATA_MODE, getSavedGems, removeSavedGem as apiRemove, upsertSavedGem } from "./api";
import {
  makeSavedGem,
  rebuildTagRegistry,
  withTag,
  withoutTag,
  type Confidence,
  type SavedGem,
  type SavedVerdict,
  type Stage,
  type TagCount,
} from "./saved-core";

export * from "./saved-core";

const KEY = "fielddesk.saved";
const TAGS_KEY = "fielddesk.tags";
const LIVE = DATA_MODE === "live";

// ---------------------------------------------------------------------------
// API <-> store mapping
// ---------------------------------------------------------------------------

function fromApi(r: ApiSavedGem): SavedGem {
  const savedAt = r.saved_at ?? new Date().toISOString();
  return {
    advertiserId: r.advertiser_id,
    pageName: r.page_name ?? "",
    appName: r.app_name,
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
  };
}

function toUpsert(gem: SavedGem, userEmail: string): ApiSavedGemIn {
  const s = gem.scoreSnapshot;
  return {
    user_email: userEmail,
    page_name: gem.pageName || null,
    app_name: gem.appName,
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
  };
}

// ---------------------------------------------------------------------------
// Store (in-memory cache; persistence = backend in live, localStorage in mock)
// ---------------------------------------------------------------------------

type SavedMap = Record<number, SavedGem>;

let cache: SavedMap | null = null;
let cachedList: SavedGem[] = [];
const listeners = new Set<() => void>();
let storageHandler: ((e: StorageEvent) => void) | null = null;
let userEmail: string | null = null;
let hydratedKey: string | null = null;

function recompute() {
  cachedList = Object.values(cache ?? {}).sort((a, b) => b.savedAt.localeCompare(a.savedAt));
}

function notify() {
  listeners.forEach((l) => l());
}

function localRead(): SavedMap {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as SavedMap) : {};
  } catch {
    return {};
  }
}

function localWrite(map: SavedMap) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(KEY, JSON.stringify(map));
    window.localStorage.setItem(TAGS_KEY, JSON.stringify(rebuildTagRegistry(map)));
  } catch {
    /* quota / private mode */
  }
}

/** Lazily seed the in-memory cache: empty in live (hydrate fills it), from
 * localStorage in mock. */
function ensureCache(): SavedMap {
  if (cache) return cache;
  cache = LIVE ? {} : localRead();
  recompute();
  return cache;
}

function onSyncError(e: unknown) {
  console.error("saved: backend sync failed", e);
  hydratedKey = null; // force a re-hydrate to resync the optimistic state
  void hydrate();
}

/** Persist a single change. Live → backend; mock → localStorage snapshot. */
function persist(map: SavedMap, changed: SavedGem | null, removedId?: number) {
  if (LIVE) {
    if (!userEmail) return;
    if (removedId != null) apiRemove(removedId, userEmail).catch(onSyncError);
    else if (changed) upsertSavedGem(changed.advertiserId, toUpsert(changed, userEmail)).catch(onSyncError);
  } else {
    localWrite(map);
  }
}

async function hydrate() {
  const key = `${DATA_MODE}:${userEmail ?? ""}`;
  if (hydratedKey === key) return;
  hydratedKey = key;
  if (!LIVE) {
    ensureCache();
    notify();
    return;
  }
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

/** Set the current user (live mode keys saved gems by their email) and refetch. */
export function setUser(email: string | null) {
  if (userEmail === email) return;
  userEmail = email;
  hydratedKey = null;
  cache = LIVE ? {} : localRead();
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
  persist(cache, updated);
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
  persist(cache, gem);
}
export function unsave(id: number): void {
  const map = ensureCache();
  if (!map[id]) return;
  const next = { ...map };
  delete next[id];
  cache = next;
  recompute();
  notify();
  persist(cache, null, id);
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

// --- external store (for useSyncExternalStore) ---
export function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  ensureCache();
  void hydrate();
  if (!storageHandler && !LIVE && typeof window !== "undefined") {
    storageHandler = (e: StorageEvent) => {
      if (e.key === KEY) {
        cache = localRead();
        recompute();
        notify();
      }
    };
    window.addEventListener("storage", storageHandler);
  }
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0 && storageHandler && typeof window !== "undefined") {
      window.removeEventListener("storage", storageHandler);
      storageHandler = null;
    }
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
