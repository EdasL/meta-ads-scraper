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
  { id: "just_saw", label: "just saw" },
  { id: "watching", label: "watching" },
  { id: "looks_real", label: "looks real" },
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
  /** Store icon captured at save time. Not persisted server-side yet, so it is
   * absent after a backend rehydrate — the UI then shows the initials tile. */
  iconUrl?: string;
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
