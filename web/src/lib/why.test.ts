/**
 * Pure-logic tests for lib/why.ts. Zero-dependency: run with
 *   node --test src/lib/why.test.ts
 * (Node 22 strips TS types at load; excluded from tsc/Next via tsconfig.)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  scoreBand,
  statusChip,
  velocityTrend,
  signedPercent,
  growthLine,
  dominantSignal,
  whyLine,
  becauseLine,
  trueAdCount,
  adsInLibraryLabel,
  signalTier,
  signalChips,
  pushPull,
  trendFromScaling,
} from "./why.ts";

const row = (over = {}) => ({
  advertiser_id: 1,
  cohort: "Health & Fitness · US",
  page_name: "Acme",
  app_id: 9,
  app_name: "Acme",
  gem_score: 80,
  scaling_velocity: 0.9,
  under_radar: 0.8,
  funnel_intent: 0.7,
  active_ad_count: 40,
  max_longevity_days: 20,
  alert: true,
  refreshed_at: "2026-06-01T00:00:00Z",
  ...over,
});

test("scoreBand thresholds", () => {
  assert.equal(scoreBand(90), "rare gem");
  assert.equal(scoreBand(85), "rare gem");
  assert.equal(scoreBand(70), "strong");
  assert.equal(scoreBand(69), "maybe");
  assert.equal(scoreBand(50), "maybe");
  assert.equal(scoreBand(49), "weak");
});

test("statusChip: fast + short-lived is a new spark", () => {
  assert.equal(statusChip({ scaling_velocity: 0.85, max_longevity_days: 12 }), "new spark");
});
test("statusChip: fast + sustained is hot now", () => {
  assert.equal(statusChip({ scaling_velocity: 0.85, max_longevity_days: 40 }), "hot now");
});
test("statusChip: mid is steady, low is cooling", () => {
  assert.equal(statusChip({ scaling_velocity: 0.5, max_longevity_days: 30 }), "steady");
  assert.equal(statusChip({ scaling_velocity: 0.2, max_longevity_days: 30 }), "cooling");
});

test("velocityTrend glyphs + tone", () => {
  assert.equal(velocityTrend(0.9).glyph, "↗");
  assert.equal(velocityTrend(0.9).tone, "up");
  assert.equal(velocityTrend(0.7).glyph, "↗");
  assert.equal(velocityTrend(0.5).glyph, "→");
  assert.equal(velocityTrend(0.5).tone, "flat");
  assert.equal(velocityTrend(0.2).glyph, "↘");
  assert.equal(velocityTrend(0.2).tone, "down");
});

test("growthLine prefers real growth numbers", () => {
  const g = growthLine({ first_count: 3, latest_count: 11, weeks: 4, pct_change: 267 }, { scaling_velocity: 0.9 });
  assert.equal(g.glyph, "↗");
  assert.equal(g.tone, "up");
  assert.match(g.text, /\+267% · 3→11 ads in 4wk/);
});

test("signedPercent: whole percent with sign, raw API floats never leak", () => {
  assert.equal(signedPercent(166.6999969482422), "+167%");
  assert.equal(signedPercent(267), "+267%");
  assert.equal(signedPercent(-12.4), "-12%");
  assert.equal(signedPercent(0.2), "0%");
  assert.equal(signedPercent(0), "0%");
  assert.equal(signedPercent(NaN), "—");
});

test("growthLine rounds raw API float pct_change to a whole percent", () => {
  const g = growthLine(
    { first_count: 30, latest_count: 80, weeks: 16, pct_change: 166.6999969482422 },
    { scaling_velocity: 0.9 },
  );
  assert.equal(g.text, "+167% · 30→80 ads in 16wk");
  assert.equal(g.tone, "up");
  // tiny float that rounds to zero → flat, not a misleading "+0%" with ↗
  const flat = growthLine(
    { first_count: 10, latest_count: 10, weeks: 4, pct_change: 0.3 },
    { scaling_velocity: 0.9 },
  );
  assert.equal(flat.text, "0% · 10→10 ads in 4wk");
  assert.equal(flat.tone, "flat");
  assert.equal(flat.glyph, "→");
});

test("growthLine: null pct_change (grew from zero) shows honest counts, no %", () => {
  const g = growthLine({ first_count: 0, latest_count: 8, weeks: 3, pct_change: null }, { scaling_velocity: 0.9 });
  assert.equal(g.tone, "up");
  assert.match(g.text, /0→8 ads in 3wk/);
  assert.ok(!g.text.includes("%"));
});

test("growthLine: single-week / missing growth falls back to velocity word", () => {
  const oneWeek = growthLine({ first_count: 5, latest_count: 5, weeks: 1, pct_change: null }, { scaling_velocity: 0.9 });
  assert.equal(oneWeek.text, "Surging");
  const none = growthLine(null, { scaling_velocity: 0.5 });
  assert.equal(none.text, "Steady");
  assert.equal(none.tone, "flat");
});

test("dominantSignal picks weighted max", () => {
  // velocity*0.5=0.45, ur*0.35=0.28, fi*0.15=0.105 -> velocity
  assert.equal(dominantSignal({ scaling_velocity: 0.9, under_radar: 0.8, funnel_intent: 0.7 }), "velocity");
  // velocity*0.5=0.05, ur*0.35=0.315, fi*0.15=0.135 -> under_radar
  assert.equal(dominantSignal({ scaling_velocity: 0.1, under_radar: 0.9, funnel_intent: 0.9 }), "under_radar");
});

test("whyLine + becauseLine name the dominant signal (plain voice)", () => {
  assert.match(whyLine(row()), /climbing fast/i);
  assert.match(becauseLine(row()), /rising fast/i);
  const ur = row({ scaling_velocity: 0.1, under_radar: 0.95, funnel_intent: 0.2 });
  assert.match(whyLine(ur), /radar/i);
  assert.match(becauseLine(ur), /you're early/i);
});

test("trueAdCount prefers meta_active_ads, falls back to active_ad_count", () => {
  assert.deepEqual(trueAdCount(row()), { count: 40, capped: false });
  assert.deepEqual(trueAdCount(row({ meta_active_ads: 87 })), { count: 87, capped: false });
  assert.deepEqual(
    trueAdCount(row({ meta_active_ads: 250, meta_active_ads_capped: true })),
    { count: 250, capped: true },
  );
  // explicit null (older API) → matview fallback
  assert.deepEqual(trueAdCount(row({ meta_active_ads: null })), { count: 40, capped: false });
  // meta count of 0 is a real value, not a missing one
  assert.deepEqual(trueAdCount(row({ meta_active_ads: 0 })), { count: 0, capped: false });
});

test("adsInLibraryLabel renders the capped form with a plus", () => {
  assert.equal(adsInLibraryLabel(row({ meta_active_ads: 87 })), "87 ads in library");
  assert.equal(
    adsInLibraryLabel(row({ meta_active_ads: 250, meta_active_ads_capped: true })),
    "250+ ads in library",
  );
  assert.equal(adsInLibraryLabel(row()), "40 ads in library");
});

test("becauseLine uses the true ad-library count when present", () => {
  assert.match(becauseLine(row({ meta_active_ads: 87 })), /87 ads in the library/);
  assert.match(
    becauseLine(row({ meta_active_ads: 250, meta_active_ads_capped: true })),
    /250\+ ads in the library/,
  );
  // without it, the matview count still shows
  assert.match(becauseLine(row()), /40 ads in the library/);
});

test("signalTier buckets", () => {
  assert.equal(signalTier(0.8), "big");
  assert.equal(signalTier(0.5), "some");
  assert.equal(signalTier(0.2), "small");
});

test("signalChips returns three, filled by strength", () => {
  const chips = signalChips({ scaling_velocity: 0.9, under_radar: 0.5, funnel_intent: 0.2 });
  assert.equal(chips.length, 3);
  assert.equal(chips[0].filled, true); // big
  assert.equal(chips[1].filled, true); // some
  assert.equal(chips[2].filled, false); // small
});

test("pushPull splits push vs pull", () => {
  const { push, pull } = pushPull({ scaling_velocity: 0.9, under_radar: 0.8, funnel_intent: 0.2 });
  assert.equal(push.length, 2);
  assert.equal(pull.length, 1);
  assert.equal(pull[0].key, "funnel_intent");
});

test("trendFromScaling computes real pct/multiple/dir", () => {
  const pts = [
    { week_start: "2026-05-04", active_ad_count: 6, distinct_creative_count: 3 },
    { week_start: "2026-06-01", active_ad_count: 47, distinct_creative_count: 19 },
  ];
  const t = trendFromScaling(pts);
  assert.equal(t.dir, "up");
  assert.equal(t.multiple, 7.8);
  assert.equal(t.pct, 683);
});

test("trendFromScaling handles sparse/zero safely", () => {
  assert.deepEqual(trendFromScaling([]), { pct: null, multiple: null, dir: "flat" });
  assert.equal(trendFromScaling([{ week_start: "a", active_ad_count: 0, distinct_creative_count: 0 }, { week_start: "b", active_ad_count: 5, distinct_creative_count: 1 }]).dir, "up");
});
