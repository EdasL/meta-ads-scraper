/**
 * saved.ts — the client-side "my gems" store (localStorage), the source of
 * truth for saving + annotating gems in this pass (backend persistence is a
 * later follow-up). Pure helpers (normalizeTag, makeSavedGem, withTag, …) are
 * separated from the storage layer so they unit-test under `node --test`.
 *
 * On save we FREEZE the score snapshot (GemScore + the 3 axes + ad count) so a
 * saved gem becomes a founder's "I called it early" record even as the live
 * metric moves on. See spec §6.
 */
import type { ShortlistRow } from "@/types/contracts";

const KEY = "fielddesk.saved";
const TAGS_KEY = "fielddesk.tags";

export type Stage = "just_saw" | "watching" | "looks_real" | "building";
export type SavedVerdict = "build" | "clone" | "pass" | "revisit";
export type Confidence = 1 | 2 | 3 | 4 | 5;

/** Ordered stage pipeline; labels are grug (product-facing). */
export const STAGES: { id: Stage; label: string }[] = [
  { id: "just_saw", label: "just saw" },
  { id: "watching", label: "watching" },
  { id: "looks_real", label: "look real" },
  { id: "building", label: "building" },
];

export const VERDICTS: { id: SavedVerdict; label: string }[] = [
  { id: "build", label: "build" },
  { id: "clone", label: "clone" },
  { id: "revisit", label: "later" },
  { id: "pass", label: "pass" },
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
  cohort: string;
  savedAt: string; // ISO
  scoreSnapshot: ScoreSnapshot;
  tags: string[];
  note: string;
  stage: Stage;
  verdict?: SavedVerdict;
  confidence?: Confidence;
}

export interface TagCount {
  name: string;
  uses: number;
}

// ---------------------------------------------------------------------------
// Pure helpers (testable, no window / no Date.now)
// ---------------------------------------------------------------------------

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
    cohort: row.cohort,
    savedAt,
    scoreSnapshot: makeSnapshot(row, savedAt),
    tags: [],
    note: "",
    stage: "just_saw",
  };
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

// ---------------------------------------------------------------------------
// Storage layer + external store (browser only; guarded for SSR)
// ---------------------------------------------------------------------------

type SavedMap = Record<string, SavedGem>;

let cache: SavedMap | null = null;
let cachedList: SavedGem[] = [];
const listeners = new Set<() => void>();
let wired = false;

function read(): SavedMap {
  if (cache) return cache;
  if (typeof window === "undefined") {
    cache = {};
  } else {
    try {
      const raw = window.localStorage.getItem(KEY);
      cache = raw ? (JSON.parse(raw) as SavedMap) : {};
    } catch {
      cache = {};
    }
  }
  recomputeList();
  return cache;
}

function recomputeList() {
  cachedList = Object.values(cache ?? {}).sort((a, b) => b.savedAt.localeCompare(a.savedAt));
}

function commit(next: SavedMap) {
  cache = next;
  recomputeList();
  if (typeof window !== "undefined") {
    try {
      window.localStorage.setItem(KEY, JSON.stringify(next));
      window.localStorage.setItem(TAGS_KEY, JSON.stringify(rebuildTagRegistry(next)));
    } catch {
      /* quota / private mode — keep in-memory copy */
    }
  }
  listeners.forEach((l) => l());
}

function now(): string {
  return new Date().toISOString();
}

function mutate(id: number, fn: (gem: SavedGem) => SavedGem) {
  const map = read();
  const gem = map[id];
  if (!gem) return;
  commit({ ...map, [id]: fn(gem) });
}

// --- queries ---
export function listSaved(): SavedGem[] {
  read();
  return cachedList;
}
export function getSaved(id: number): SavedGem | undefined {
  return read()[id];
}
export function isSaved(id: number): boolean {
  return !!read()[id];
}
export function allTags(): TagCount[] {
  return rebuildTagRegistry(read());
}

// --- mutations ---
export function save(row: ShortlistRow): void {
  const map = read();
  if (map[row.advertiser_id]) return; // keep existing snapshot/notes
  commit({ ...map, [row.advertiser_id]: makeSavedGem(row, now()) });
}
export function unsave(id: number): void {
  const map = read();
  if (!map[id]) return;
  const next = { ...map };
  delete next[id];
  commit(next);
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
  if (!wired && typeof window !== "undefined") {
    wired = true;
    window.addEventListener("storage", (e) => {
      if (e.key === KEY) {
        cache = null; // re-read on next access (cross-tab change)
        read();
        listeners.forEach((l) => l());
      }
    });
  }
  return () => listeners.delete(listener);
}
export function getSnapshot(): SavedGem[] {
  read();
  return cachedList;
}
// Must be a stable reference — useSyncExternalStore loops if this changes.
const EMPTY: SavedGem[] = [];
export function getServerSnapshot(): SavedGem[] {
  return EMPTY;
}
