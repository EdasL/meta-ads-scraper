/**
 * Pure-logic tests for lib/why.ts. Zero-dependency: run with
 *   node --test src/lib/why.test.ts
 * (Node 22 strips TS types at load; excluded from tsc/Next via tsconfig.)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { signedPercent, growthLine, trendFromScaling, monthLabel } from "./why.ts";

// GemScore v3: ad-count direction is not a quality signal, so growthLine is always
// neutral (glyph null, tone flat). The real count/percent text still renders.
test("growthLine prefers real growth numbers (base >= 5 keeps the percent)", () => {
  const g = growthLine({ first_count: 5, latest_count: 20, weeks: 4, pct_change: 300 }, { active_ad_count: 40 });
  assert.equal(g.glyph, null);
  assert.equal(g.tone, "flat");
  assert.equal(g.kind, "growth");
  assert.match(g.text, /\+300% · 5→20 ads in 4 weeks/);
});

test("growthLine never renders a percent from a base under 5 ads", () => {
  // 3→11 with pct_change present: tiny base → counts only, no %
  const g = growthLine({ first_count: 3, latest_count: 11, weeks: 4, pct_change: 267 }, { active_ad_count: 40 });
  assert.equal(g.kind, "growth");
  assert.equal(g.glyph, null);
  assert.equal(g.tone, "flat");
  assert.equal(g.text, "3→11 ads in 4 weeks");
  assert.ok(!g.text.includes("%"));
  // shrinking count → still the real delta, but neutral (consolidation isn't decline)
  const down = growthLine({ first_count: 4, latest_count: 1, weeks: 6, pct_change: -75 }, { active_ad_count: 1 });
  assert.equal(down.glyph, null);
  assert.equal(down.tone, "flat");
  assert.equal(down.text, "4→1 ads in 6 weeks");
});

test("growthLine anchors on the first-ad month when the row carries first_ad_at", () => {
  const now = Date.parse("2026-06-10T12:00:00Z");
  // same year → month only
  const g = growthLine(
    { first_count: 2, latest_count: 25, weeks: 16, pct_change: 1150 },
    { active_ad_count: 25, first_ad_at: "2026-03-02" },
    now,
  );
  assert.equal(g.text, "2→25 ads since Mar");
  assert.equal(g.glyph, null);
  assert.ok(!g.text.includes("%")); // tiny base: no percent even with first_ad_at
  // earlier year → month + year
  const old = growthLine(
    { first_count: 2, latest_count: 8, weeks: 16, pct_change: null },
    { active_ad_count: 8, first_ad_at: "2025-07-10" },
    now,
  );
  assert.equal(old.text, "2→8 ads since Jul 2025");
  // base >= 5: percent + the since copy together
  const pct = growthLine(
    { first_count: 10, latest_count: 25, weeks: 12, pct_change: 150 },
    { active_ad_count: 25, first_ad_at: "2026-03-02" },
    now,
  );
  assert.equal(pct.text, "+150% · 10→25 ads since Mar");
  // garbage first_ad_at → weeks fallback, never a broken label
  const bad = growthLine(
    { first_count: 2, latest_count: 8, weeks: 3, pct_change: null },
    { active_ad_count: 8, first_ad_at: "not-a-date" },
    now,
  );
  assert.equal(bad.text, "2→8 ads in 3 weeks");
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
  assert.equal(g.text, "+167% · 30→80 ads in 16 weeks");
  assert.equal(g.tone, "flat");
  assert.equal(g.glyph, null);
  // tiny float that rounds to zero → still the real "0% · 10→10" text, neutral
  const flat = growthLine(
    { first_count: 10, latest_count: 10, weeks: 4, pct_change: 0.3 },
    { active_ad_count: 40 },
  );
  assert.equal(flat.text, "0% · 10→10 ads in 4 weeks");
  assert.equal(flat.tone, "flat");
  assert.equal(flat.glyph, null);
});

test("growthLine: null pct_change (grew from zero) shows honest counts, no %", () => {
  const g = growthLine({ first_count: 0, latest_count: 8, weeks: 3, pct_change: null }, { active_ad_count: 8 });
  assert.equal(g.tone, "flat");
  assert.equal(g.kind, "growth");
  assert.match(g.text, /0→8 ads in 3 weeks/);
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

test("trendFromScaling withholds pct/multiple from bases under 5 ads", () => {
  const ramp = trendFromScaling([
    { week_start: "2026-02-23", active_ad_count: 2, distinct_creative_count: 0 },
    { week_start: "2026-06-01", active_ad_count: 25, distinct_creative_count: 0 },
  ]);
  assert.deepEqual(ramp, { pct: null, multiple: null, dir: "up" });
  const cull = trendFromScaling([
    { week_start: "2026-02-23", active_ad_count: 4, distinct_creative_count: 0 },
    { week_start: "2026-06-01", active_ad_count: 1, distinct_creative_count: 0 },
  ]);
  assert.deepEqual(cull, { pct: null, multiple: null, dir: "down" });
});

test("monthLabel drops the year only for the current year", () => {
  const now = Date.parse("2026-06-12T00:00:00Z");
  assert.equal(monthLabel("2026-03-04", now), "Mar");
  assert.equal(monthLabel("2025-11-20", now), "Nov 2025");
  assert.equal(monthLabel("garbage", now), null);
  assert.equal(monthLabel(null, now), null);
  assert.equal(monthLabel(undefined, now), null);
});
