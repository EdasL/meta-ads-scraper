/**
 * why.ts — turns the raw GemScore axes (scaling_velocity / under_radar /
 * funnel_intent, each 0-1) into the human-facing, grug-voice explanation the
 * UI renders: the score band, a status chip, the dominant "because" reason,
 * per-signal strength, and a real trend computed from the weekly scaling array.
 *
 * Pure functions only (no React, no DOM) so they unit-test under `node --test`.
 * Honesty rule: we never invent a percentage from a 0-1 scalar. The feed card
 * shows a QUALITATIVE velocity trend; a real % is derived only from the actual
 * weekly `scaling` points on the detail page (see `trendFromScaling`).
 */
import type { ScalingPoint, ShortlistRow } from "@/types/contracts";

/** GemScore weights (mirror src/agf/scoring/gemscore.py: SV^.5·UR^.35·FI^.15). */
const WEIGHTS = { scaling_velocity: 0.5, under_radar: 0.35, funnel_intent: 0.15 } as const;

export type SignalKey = keyof typeof WEIGHTS;
export type DominantSignal = "velocity" | "under_radar" | "funnel";
export type ScoreBand = "weak" | "maybe" | "strong" | "rare gem";
export type StatusChip = "new spark" | "hot now" | "steady" | "cooling";
export type SignalTier = "big" | "some" | "small";

/** Named GemScore bands (grug). Avoids exposing raw cutoffs to the user. */
export function scoreBand(gemScore: number): ScoreBand {
  if (gemScore >= 85) return "rare gem";
  if (gemScore >= 70) return "strong";
  if (gemScore >= 50) return "maybe";
  return "weak";
}

/**
 * Status chip from current velocity + how long ads have run. A short-lived but
 * fast mover is a "new spark"; sustained fast is "hot now"; mid is "steady";
 * slow is "cooling".
 */
export function statusChip(row: Pick<ShortlistRow, "scaling_velocity" | "max_longevity_days">): StatusChip {
  const v = row.scaling_velocity;
  if (v >= 0.8 && row.max_longevity_days <= 16) return "new spark";
  if (v >= 0.7) return "hot now";
  if (v >= 0.45) return "steady";
  return "cooling";
}

/** Qualitative velocity trend for the feed card (no fake %). */
export function velocityTrend(scalingVelocity: number): { glyph: "↗" | "→" | "↘"; word: string } {
  if (scalingVelocity >= 0.85) return { glyph: "↗", word: "fast climb" };
  if (scalingVelocity >= 0.66) return { glyph: "↗", word: "climbing" };
  if (scalingVelocity >= 0.45) return { glyph: "→", word: "steady" };
  return { glyph: "↘", word: "cooling" };
}

/** Which axis is driving the score most (weighted contribution). */
export function dominantSignal(
  row: Pick<ShortlistRow, "scaling_velocity" | "under_radar" | "funnel_intent">,
): DominantSignal {
  const contrib: Record<DominantSignal, number> = {
    velocity: row.scaling_velocity * WEIGHTS.scaling_velocity,
    under_radar: row.under_radar * WEIGHTS.under_radar,
    funnel: row.funnel_intent * WEIGHTS.funnel_intent,
  };
  let best: DominantSignal = "velocity";
  for (const k of Object.keys(contrib) as DominantSignal[]) {
    if (contrib[k] > contrib[best]) best = k;
  }
  return best;
}

/** One-line grug "why" for the feed card. */
export function whyLine(row: ShortlistRow): string {
  switch (dominantSignal(row)) {
    case "velocity":
      return `spend climb fast. ${row.active_ad_count} ad live now.`;
    case "under_radar":
      return "still hidden. almost nobody run this yet.";
    case "funnel":
      return "real funnel — they take money, not just email.";
  }
}

/** Fuller one-sentence verdict for the detail page (names dominant signal). */
export function becauseLine(row: ShortlistRow): string {
  switch (dominantSignal(row)) {
    case "velocity":
      return `grug think hot — spend go up fast, and ${row.active_ad_count} ad run now.`;
    case "under_radar":
      return "big spend but almost nobody run ad like this yet. you early.";
    case "funnel":
      return "real checkout and price, not just grab email. they make money.";
  }
}

export function signalTier(value: number): SignalTier {
  if (value >= 0.66) return "big";
  if (value >= 0.4) return "some";
  return "small";
}

/** Static metadata for each signal: short label, grug label, glossary. */
export const SIGNALS: { key: SignalKey; short: string; label: string; glossary: string }[] = [
  {
    key: "scaling_velocity",
    short: "scaling",
    label: "scaling fast",
    glossary: "how fast they grow ad spend. fast = good. you want the climb.",
  },
  {
    key: "under_radar",
    short: "radar",
    label: "under radar",
    glossary: "how hidden it still is. nobody know yet = good. you get there first.",
  },
  {
    key: "funnel_intent",
    short: "funnel",
    label: "real funnel",
    glossary: "do they really sell? checkout + price + upsell = real money, not just email grab.",
  },
];

export const GEMSCORE_GLOSSARY =
  "gemscore = how good the find is, 0 to 100. high = fast climb, still hidden, real funnel.";

export interface SignalChip {
  key: SignalKey;
  label: string;
  tier: SignalTier;
  value: number;
  filled: boolean;
}

/** The three signal chips for a card, with fill driven by strength. */
export function signalChips(
  row: Pick<ShortlistRow, "scaling_velocity" | "under_radar" | "funnel_intent">,
): SignalChip[] {
  return SIGNALS.map((s) => {
    const value = row[s.key];
    const tier = signalTier(value);
    return { key: s.key, label: s.label, tier, value, filled: tier !== "small" };
  });
}

export interface PushPull {
  push: SignalChip[];
  pull: SignalChip[];
}

/** Split signals into what lifts the score (push) vs what holds it back (pull). */
export function pushPull(
  row: Pick<ShortlistRow, "scaling_velocity" | "under_radar" | "funnel_intent">,
): PushPull {
  const chips = signalChips(row);
  return {
    push: chips.filter((c) => c.tier !== "small"),
    pull: chips.filter((c) => c.tier === "small"),
  };
}

export interface Trend {
  pct: number | null;
  multiple: number | null;
  dir: "up" | "flat" | "down";
}

/** Real trend from the weekly scaling array (detail page only). */
export function trendFromScaling(points: ScalingPoint[]): Trend {
  if (points.length < 2) return { pct: null, multiple: null, dir: "flat" };
  const first = points[0].active_ad_count;
  const last = points[points.length - 1].active_ad_count;
  if (first <= 0) return { pct: null, multiple: null, dir: last > 0 ? "up" : "flat" };
  const pct = Math.round(((last - first) / first) * 100);
  const multiple = Math.round((last / first) * 10) / 10;
  const dir = pct > 8 ? "up" : pct < -8 ? "down" : "flat";
  return { pct, multiple, dir };
}
