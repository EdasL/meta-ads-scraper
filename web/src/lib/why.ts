/**
 * why.ts — turns the raw GemScore axes (scaling_velocity / under_radar /
 * funnel_intent, each 0-1) into the human-facing explanation the UI renders:
 * the score band, a status chip, the dominant "because" reason, per-signal
 * strength, and a real trend computed from the weekly scaling array.
 *
 * Voice note: the main UI is plain/clear (this is the inverted voice — grug is
 * confined to the GrugExplainer component only). Pure functions only (no React,
 * no DOM) so they unit-test under `node --test`. Honesty rule: we never invent a
 * percentage from a 0-1 scalar. The feed card prefers the real `growth` numbers;
 * otherwise it shows a QUALITATIVE velocity trend. A real % is derived from the
 * actual weekly `scaling` points on the detail page (see `trendFromScaling`).
 */
import type { Growth, ScalingPoint, ShortlistRow } from "@/types/contracts";

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

export type TrendTone = "up" | "down" | "flat";

/** Qualitative velocity trend for the feed card (no fake %). */
export function velocityTrend(scalingVelocity: number): { glyph: "↗" | "→" | "↘"; word: string; tone: TrendTone } {
  if (scalingVelocity >= 0.85) return { glyph: "↗", word: "Surging", tone: "up" };
  if (scalingVelocity >= 0.66) return { glyph: "↗", word: "Climbing", tone: "up" };
  if (scalingVelocity >= 0.45) return { glyph: "→", word: "Steady", tone: "flat" };
  return { glyph: "↘", word: "Cooling", tone: "down" };
}

/**
 * The card's quantified growth line. Prefers the real `growth` numbers
 * ("↗ +267% · 3→11 ads in 4wk"); when growth is sparse (single week, or from
 * zero) but counts moved, shows the count delta without a fake %; otherwise
 * falls back to the qualitative velocity word. Returns the glyph, text, and a
 * tone for color.
 */
export function growthLine(
  growth: Growth | null | undefined,
  fallback: { scaling_velocity: number },
): { glyph: "↗" | "→" | "↘"; text: string; tone: TrendTone } {
  if (growth && growth.weeks >= 2) {
    const { first_count, latest_count, weeks, pct_change } = growth;
    const counts = `${first_count}→${latest_count} ads in ${weeks}wk`;
    if (pct_change != null) {
      const tone: TrendTone = pct_change > 0 ? "up" : pct_change < 0 ? "down" : "flat";
      const glyph = tone === "up" ? "↗" : tone === "down" ? "↘" : "→";
      const sign = pct_change > 0 ? "+" : "";
      return { glyph, text: `${sign}${pct_change}% · ${counts}`, tone };
    }
    // pct_change null (e.g. grew from zero) but we still have honest counts.
    const tone: TrendTone = latest_count > first_count ? "up" : latest_count < first_count ? "down" : "flat";
    const glyph = tone === "up" ? "↗" : tone === "down" ? "↘" : "→";
    return { glyph, text: counts, tone };
  }
  // No usable growth series → qualitative velocity word (no fake %).
  const vt = velocityTrend(fallback.scaling_velocity);
  return { glyph: vt.glyph, text: vt.word, tone: vt.tone };
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

/** One-line plain "why" for the feed card. */
export function whyLine(row: ShortlistRow): string {
  switch (dominantSignal(row)) {
    case "velocity":
      return `Ad spend is climbing fast — ${row.active_ad_count} ads live now.`;
    case "under_radar":
      return "Still under the radar — almost nobody runs ads like this yet.";
    case "funnel":
      return "Real funnel — they take payment, not just emails.";
  }
}

/** Row slice carrying the (optional) true ad-library count from the dossier. */
export type AdCountSource = Pick<ShortlistRow, "active_ad_count"> & {
  meta_active_ads?: number | null;
  meta_active_ads_capped?: boolean | null;
};

/**
 * The ad count the detail page should show. Prefers the true ad-library count
 * (`meta_active_ads`, from bounded pagination — `capped` true when it hit the
 * cap, render as "N+"); falls back to the matview's `active_ad_count`.
 * Discover cards keep using `active_ad_count` directly.
 */
export function trueAdCount(row: AdCountSource): { count: number; capped: boolean } {
  if (row.meta_active_ads != null) {
    return { count: row.meta_active_ads, capped: Boolean(row.meta_active_ads_capped) };
  }
  return { count: row.active_ad_count, capped: false };
}

/** "N ads in library" / "250+ ads in library" — for the detail verdict line. */
export function adsInLibraryLabel(row: AdCountSource): string {
  const { count, capped } = trueAdCount(row);
  return `${count}${capped ? "+" : ""} ads in library`;
}

/** Fuller one-sentence verdict for the detail page (names dominant signal). */
export function becauseLine(row: ShortlistRow & Partial<AdCountSource>): string {
  const { count, capped } = trueAdCount(row);
  switch (dominantSignal(row)) {
    case "velocity":
      return `Spend is rising fast, with ${count}${capped ? "+" : ""} ads in the library now.`;
    case "under_radar":
      return "Spending heavily, but almost nobody runs ads like this yet — you're early.";
    case "funnel":
      return "A real checkout with pricing, not just an email grab. They make money.";
  }
}

export function signalTier(value: number): SignalTier {
  if (value >= 0.66) return "big";
  if (value >= 0.4) return "some";
  return "small";
}

/** Static metadata for each signal: short label, display label, glossary. */
export const SIGNALS: { key: SignalKey; short: string; label: string; glossary: string }[] = [
  {
    key: "scaling_velocity",
    short: "scaling",
    label: "Scaling",
    glossary: "How fast they're growing ad spend. A steep climb is the strongest signal.",
  },
  {
    key: "under_radar",
    short: "radar",
    label: "Under radar",
    glossary: "How hidden it still is. Few competitors run ads like this yet — you'd be early.",
  },
  {
    key: "funnel_intent",
    short: "funnel",
    label: "Real funnel",
    glossary: "Whether they actually sell: checkout, pricing, and upsell — real revenue, not just email capture.",
  },
];

export const GEMSCORE_GLOSSARY =
  "GemScore rates how promising a find is, 0 to 100. High = scaling fast, still under the radar, and a real funnel.";

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
