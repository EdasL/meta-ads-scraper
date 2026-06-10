/**
 * Pure-logic tests for lib/why.ts. Zero-dependency: run with
 *   node --test src/lib/why.test.ts
 * (Node 22 strips TS types at load; excluded from tsc/Next via tsconfig.)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { signedPercent, growthLine, trendFromScaling } from "./why.ts";

test("growthLine prefers real growth numbers", () => {
  const g = growthLine({ first_count: 3, latest_count: 11, weeks: 4, pct_change: 267 }, { active_ad_count: 40 });
  assert.equal(g.glyph, "↗");
  assert.equal(g.tone, "up");
  assert.equal(g.kind, "growth");
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
    { active_ad_count: 40 },
  );
  assert.equal(g.text, "+167% · 30→80 ads in 16wk");
  assert.equal(g.tone, "up");
  // tiny float that rounds to zero → flat, not a misleading "+0%" with ↗
  const flat = growthLine(
    { first_count: 10, latest_count: 10, weeks: 4, pct_change: 0.3 },
    { active_ad_count: 40 },
  );
  assert.equal(flat.text, "0% · 10→10 ads in 4wk");
  assert.equal(flat.tone, "flat");
  assert.equal(flat.glyph, "→");
});

test("growthLine: null pct_change (grew from zero) shows honest counts, no %", () => {
  const g = growthLine({ first_count: 0, latest_count: 8, weeks: 3, pct_change: null }, { active_ad_count: 8 });
  assert.equal(g.tone, "up");
  assert.equal(g.kind, "growth");
  assert.match(g.text, /0→8 ads in 3wk/);
  assert.ok(!g.text.includes("%"));
});

test("growthLine: single-week / missing growth falls back to the real ad count", () => {
  const oneWeek = growthLine({ first_count: 5, latest_count: 5, weeks: 1, pct_change: null }, { active_ad_count: 5 });
  assert.equal(oneWeek.kind, "count");
  assert.equal(oneWeek.text, "5");
  assert.equal(oneWeek.glyph, null);
  const none = growthLine(null, { active_ad_count: 12 });
  assert.equal(none.kind, "count");
  assert.equal(none.text, "12");
  assert.equal(none.glyph, null);
  assert.equal(none.tone, "flat");
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
