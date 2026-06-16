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

/** Percents (and multiples) from a base smaller than this are noise — a 2→25
 * launch ramp is real growth but "+1150%" reads as fake. Never render them. */
export const MIN_PCT_BASE = 5;

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "Mar" (current year) or "Mar 2025" from an ISO date.
 * String-parsed so a date-only value never shifts a month across timezones.
 * Returns null on garbage input so callers fall back or render a dash. */
export function monthLabel(iso: string | null | undefined, nowMs: number = Date.now()): string | null {
  if (!iso) return null;
  const m = /^(\d{4})-(\d{2})/.exec(iso);
  if (!m) return null;
  const month = MONTHS[Number(m[2]) - 1];
  if (!month) return null;
  const year = Number(m[1]);
  return year === new Date(nowMs).getFullYear() ? month : `${month} ${year}`;
}

function sinceLabel(iso: string, nowMs: number): string | null {
  const label = monthLabel(iso, nowMs);
  return label === null ? null : `since ${label}`;
}

/** Whole-number percent with an explicit sign: 166.6999 → "+167%", -3.2 →
 * "-3%", 0.2 → "0%". Raw API floats are never rendered. */
export function signedPercent(pct: number): string {
  if (!Number.isFinite(pct)) return "—";
  const r = Math.round(pct);
  return `${r > 0 ? "+" : ""}${r}%`;
}

/**
 * The card's growth line, built only from observed ad counts. With a usable
 * `growth` series it quantifies the move, anchored on the first stored ad
 * when the row carries `first_ad_at` ("↗ 2→25 ads since Mar"), else on the
 * series window ("↗ 3→11 ads in 4 weeks"). A percent renders only when the
 * base is at least MIN_PCT_BASE ads AND pct_change exists — tiny bases make
 * giant fake-looking percents. With no usable series (`kind: "count"`) it
 * falls back to the one real fact the row carries — the live ad count.
 *
 * GemScore v3 (2026-06-16): ad-count direction is NO LONGER a quality signal —
 * an app going 30→2 ads found its winners, it isn't declining. So we show the
 * real count delta but never a directional glyph or up/down tone: the trend is
 * always neutral (`glyph: null`, `tone: "flat"`). The number speaks; we don't
 * color it good or bad.
 *
 * `nowMs` only decides whether the "since" month needs a year; tests pin it.
 */
export function growthLine(
  growth: Growth | null | undefined,
  fallback: { active_ad_count: number; first_ad_at?: string | null },
  nowMs: number = Date.now(),
): { glyph: "↗" | "→" | "↘" | null; text: string; tone: TrendTone; kind: "growth" | "count" } {
  if (growth && growth.weeks >= 2) {
    const { first_count, latest_count, weeks, pct_change } = growth;
    const since = fallback.first_ad_at ? sinceLabel(fallback.first_ad_at, nowMs) : null;
    const counts = since
      ? `${first_count}→${latest_count} ads ${since}`
      : `${first_count}→${latest_count} ads in ${weeks} ${weeks === 1 ? "week" : "weeks"}`;
    // A percent still renders (it's a real fact) only above the base floor, but
    // its sign is the only direction cue — no glyph, no tone color.
    const text = pct_change != null && first_count >= MIN_PCT_BASE
      ? `${signedPercent(pct_change)} · ${counts}`
      : counts;
    return { glyph: null, text, tone: "flat", kind: "growth" };
  }
  // No usable growth series → the real live ad count, nothing derived.
  return { glyph: null, text: `${fallback.active_ad_count}`, tone: "flat", kind: "count" };
}

export const GEMSCORE_GLOSSARY =
  "GemScore rates how promising a find is, 0 to 100. High = proven ads running a while, growing installs and reviews, and still under the radar.";

export interface Trend {
  pct: number | null;
  multiple: number | null;
  dir: "up" | "flat" | "down";
}

/** Real trend from the weekly scaling array (detail page only). Bases under
 * MIN_PCT_BASE get no pct/multiple — direction only — same floor as the cards. */
export function trendFromScaling(points: ScalingPoint[]): Trend {
  if (points.length < 2) return { pct: null, multiple: null, dir: "flat" };
  const first = points[0].active_ad_count;
  const last = points[points.length - 1].active_ad_count;
  if (first < MIN_PCT_BASE) {
    return { pct: null, multiple: null, dir: last > first ? "up" : last < first ? "down" : "flat" };
  }
  const pct = Math.round(((last - first) / first) * 100);
  const multiple = Math.round((last / first) * 10) / 10;
  const dir = pct > 8 ? "up" : pct < -8 ? "down" : "flat";
  return { pct, multiple, dir };
}
