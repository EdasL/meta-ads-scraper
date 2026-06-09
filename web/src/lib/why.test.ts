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
  dominantSignal,
  whyLine,
  becauseLine,
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

test("velocityTrend glyphs", () => {
  assert.equal(velocityTrend(0.9).glyph, "↗");
  assert.equal(velocityTrend(0.7).glyph, "↗");
  assert.equal(velocityTrend(0.5).glyph, "→");
  assert.equal(velocityTrend(0.2).glyph, "↘");
});

test("dominantSignal picks weighted max", () => {
  // velocity*0.5=0.45, ur*0.35=0.28, fi*0.15=0.105 -> velocity
  assert.equal(dominantSignal({ scaling_velocity: 0.9, under_radar: 0.8, funnel_intent: 0.7 }), "velocity");
  // velocity*0.5=0.05, ur*0.35=0.315, fi*0.15=0.135 -> under_radar
  assert.equal(dominantSignal({ scaling_velocity: 0.1, under_radar: 0.9, funnel_intent: 0.9 }), "under_radar");
});

test("whyLine + becauseLine name the dominant signal", () => {
  assert.match(whyLine(row()), /spend climb fast/);
  assert.match(becauseLine(row()), /grug think hot/);
  const ur = row({ scaling_velocity: 0.1, under_radar: 0.95, funnel_intent: 0.2 });
  assert.match(whyLine(ur), /hidden/);
  assert.match(becauseLine(ur), /you early/);
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
