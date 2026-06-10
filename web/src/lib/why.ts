/**
 * why.ts — pure helpers that turn real ad data (the weekly scaling series and
 * the growth summary) into the numbers the UI renders. Pure functions only
 * (no React, no DOM) so they unit-test under `node --test`.
 *
 * Honesty rule: every rendered value comes from observed ad data. We never
 * invent a percentage from a 0-1 scalar, and we never wrap a number in a
 * derived qualitative word ("steady", "surging") — the numbers speak.
 */
import type { Growth, ScalingPoint } from "@/types/contracts";

export type TrendTone = "up" | "down" | "flat";

/** Whole-number percent with an explicit sign: 166.6999 → "+167%", -3.2 →
 * "-3%", 0.2 → "0%". Raw API floats are never rendered. */
export function signedPercent(pct: number): string {
  if (!Number.isFinite(pct)) return "—";
  const r = Math.round(pct);
  return `${r > 0 ? "+" : ""}${r}%`;
}

/**
 * The card's growth line, built only from observed ad counts. With a usable
 * `growth` series it quantifies the move ("↗ +267% · 3→11 ads in 4wk"; when
 * pct_change can't be computed honestly it shows the count delta alone). With
 * no usable series (`kind: "count"`) it falls back to the one real fact the
 * row carries — the live ad count — with no glyph and no invented trend word.
 */
export function growthLine(
  growth: Growth | null | undefined,
  fallback: { active_ad_count: number },
): { glyph: "↗" | "→" | "↘" | null; text: string; tone: TrendTone; kind: "growth" | "count" } {
  if (growth && growth.weeks >= 2) {
    const { first_count, latest_count, weeks, pct_change } = growth;
    const counts = `${first_count}→${latest_count} ads in ${weeks} ${weeks === 1 ? "week" : "weeks"}`;
    if (pct_change != null) {
      // Round before deriving tone so the glyph always matches the shown %.
      const rounded = Math.round(pct_change);
      const tone: TrendTone = rounded > 0 ? "up" : rounded < 0 ? "down" : "flat";
      const glyph = tone === "up" ? "↗" : tone === "down" ? "↘" : "→";
      return { glyph, text: `${signedPercent(pct_change)} · ${counts}`, tone, kind: "growth" };
    }
    // pct_change null (e.g. grew from zero) but we still have honest counts.
    const tone: TrendTone = latest_count > first_count ? "up" : latest_count < first_count ? "down" : "flat";
    const glyph = tone === "up" ? "↗" : tone === "down" ? "↘" : "→";
    return { glyph, text: counts, tone, kind: "growth" };
  }
  // No usable growth series → the real live ad count, nothing derived.
  return { glyph: null, text: `${fallback.active_ad_count}`, tone: "flat", kind: "count" };
}

export const GEMSCORE_GLOSSARY =
  "GemScore rates how promising a find is, 0 to 100. High = scaling fast, still under the radar, and a real funnel.";

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
