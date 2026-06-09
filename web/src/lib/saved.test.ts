/**
 * Pure-logic tests for lib/saved.ts helpers. Zero-dependency: run with
 *   node --test src/lib/saved.test.ts
 * Only the pure functions are tested here (the storage layer needs a DOM).
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  normalizeTag,
  makeSnapshot,
  makeSavedGem,
  withTag,
  withoutTag,
  rebuildTagRegistry,
} from "./saved.ts";

const row = (over = {}) => ({
  advertiser_id: 1001,
  cohort: "Health & Fitness · US",
  page_name: "SleepWave Labs",
  app_id: 90211,
  app_name: "SleepWave",
  gem_score: 88,
  scaling_velocity: 0.91,
  under_radar: 0.86,
  funnel_intent: 0.78,
  active_ad_count: 47,
  max_longevity_days: 31,
  alert: true,
  refreshed_at: "2026-06-04T08:00:00Z",
  ...over,
});

test("normalizeTag lowercases and collapses punctuation/space", () => {
  assert.equal(normalizeTag("  UGC! "), "ugc");
  assert.equal(normalizeTag("AI/ML"), "ai ml");
  assert.equal(normalizeTag("Sleep   App"), "sleep app");
  assert.equal(normalizeTag("###"), "");
});

test("makeSnapshot freezes the why-it-was-emerging axes", () => {
  const snap = makeSnapshot(row(), "2026-06-09T10:00:00Z");
  assert.equal(snap.gemScore, 88);
  assert.equal(snap.scalingVelocity, 0.91);
  assert.equal(snap.underRadar, 0.86);
  assert.equal(snap.funnelIntent, 0.78);
  assert.equal(snap.activeAdCount, 47);
  assert.equal(snap.capturedAt, "2026-06-09T10:00:00Z");
});

test("makeSavedGem defaults stage/tags/note and carries identity", () => {
  const g = makeSavedGem(row(), "2026-06-09T10:00:00Z");
  assert.equal(g.advertiserId, 1001);
  assert.equal(g.appName, "SleepWave");
  assert.equal(g.stage, "just_saw");
  assert.deepEqual(g.tags, []);
  assert.equal(g.note, "");
  assert.equal(g.scoreSnapshot.gemScore, 88);
});

test("withTag normalizes + dedupes; withoutTag removes; both immutable", () => {
  const g0 = makeSavedGem(row(), "t");
  const g1 = withTag(g0, "Sleep");
  const g2 = withTag(g1, "sleep"); // dupe after normalize
  assert.deepEqual(g1.tags, ["sleep"]);
  assert.deepEqual(g2.tags, ["sleep"]);
  assert.deepEqual(g0.tags, []); // original untouched
  const g3 = withTag(g2, "UGC");
  assert.deepEqual(g3.tags, ["sleep", "ugc"]);
  assert.deepEqual(withoutTag(g3, "Sleep").tags, ["ugc"]);
});

test("withTag ignores empty/punctuation-only input", () => {
  const g = withTag(makeSavedGem(row(), "t"), "  !! ");
  assert.deepEqual(g.tags, []);
});

test("rebuildTagRegistry counts and sorts by uses desc", () => {
  const a = { ...makeSavedGem(row({ advertiser_id: 1 }), "t"), tags: ["sleep", "ai"] };
  const b = { ...makeSavedGem(row({ advertiser_id: 2 }), "t"), tags: ["sleep"] };
  const reg = rebuildTagRegistry({ 1: a, 2: b });
  assert.deepEqual(reg, [
    { name: "sleep", uses: 2 },
    { name: "ai", uses: 1 },
  ]);
});
