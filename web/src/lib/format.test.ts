/**
 * Pure-logic tests for lib/format.ts. Run with
 *   node --test src/lib/format.test.ts
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  adRunLine,
  compactCount,
  creativeMetaLine,
  daysAgoLabel,
  isoDateLabel,
  monthDayLabel,
  timeAgo,
} from "./format.ts";

const NOW = Date.parse("2026-06-09T12:00:00Z");

test("timeAgo buckets", () => {
  assert.equal(timeAgo("2026-06-09T11:59:40Z", NOW), "just now");
  assert.equal(timeAgo("2026-06-09T11:30:00Z", NOW), "30m ago");
  assert.equal(timeAgo("2026-06-09T09:00:00Z", NOW), "3h ago");
  assert.equal(timeAgo("2026-06-06T12:00:00Z", NOW), "3d ago");
  assert.equal(timeAgo("2026-05-19T12:00:00Z", NOW), "3wk ago");
  assert.equal(timeAgo("2026-03-09T12:00:00Z", NOW), "3mo ago");
});

test("timeAgo handles bad input and future", () => {
  assert.equal(timeAgo("not-a-date", NOW), "—");
  assert.equal(timeAgo("2026-06-09T12:00:30Z", NOW), "just now"); // clamped to 0
});

test("daysAgoLabel buckets", () => {
  assert.equal(daysAgoLabel(0), "today");
  assert.equal(daysAgoLabel(5), "5d ago");
  assert.equal(daysAgoLabel(31), "4wk ago");
  assert.equal(daysAgoLabel(90), "3mo ago");
  assert.equal(daysAgoLabel(800), "2y ago");
});

test("compactCount: K/M buckets, small numbers verbatim, bad input safe", () => {
  assert.equal(compactCount(980), "980");
  assert.equal(compactCount(1530), "1.5K");
  assert.equal(compactCount(120000), "120K");
  assert.equal(compactCount(116000000), "116M");
  assert.equal(compactCount(NaN), "—");
});

test("monthDayLabel + isoDateLabel parse the string (no timezone day-shift)", () => {
  assert.equal(monthDayLabel("2026-04-09"), "4/9");
  assert.equal(monthDayLabel("2026-12-31"), "12/31");
  assert.equal(monthDayLabel("not-a-date"), "—");
  assert.equal(isoDateLabel("2026-02-01"), "Feb 1, 2026");
  assert.equal(isoDateLabel("2026-12-31"), "Dec 31, 2026");
  assert.equal(isoDateLabel("nope"), "—");
});

test("creativeMetaLine: since + days + EU reach + platforms; never invents", () => {
  assert.equal(
    creativeMetaLine({
      ad_started_on: "2026-04-09",
      ad_days_running: 62,
      eu_total_reach: 120000,
      publisher_platforms: ["FACEBOOK", "INSTAGRAM"],
    }),
    "since 4/9 · 62d · EU reach 120K · fb ig",
  );
  // US ad: no reach, no platforms → only start + days (no fake impressions)
  assert.equal(
    creativeMetaLine({ ad_started_on: "2026-04-09", ad_days_running: 62 }),
    "since 4/9 · 62d",
  );
  // start date without a day count
  assert.equal(creativeMetaLine({ ad_started_on: "2026-04-09" }), "since 4/9");
  // days without a start date
  assert.equal(creativeMetaLine({ ad_days_running: 5 }), "5d running");
  // unknown platform falls back to lowercase
  assert.equal(
    creativeMetaLine({ publisher_platforms: ["AUDIENCE_NETWORK", "WHATSAPP"] }),
    "audience whatsapp",
  );
  // nothing known → null (the tile renders no meta line)
  assert.equal(creativeMetaLine({}), null);
});

test("adRunLine: active ads show start + days; end date is ignored while active", () => {
  assert.equal(
    adRunLine({
      ad_started_on: "2026-01-15",
      ad_ended_on: "2026-06-10", // Meta sets end_date = last-seen even when active
      ad_active: true,
      ad_days_running: 146,
    }),
    "Started Jan 15, 2026 · 146d",
  );
  assert.equal(adRunLine({ ad_started_on: "2026-01-15", ad_active: true }), "Started Jan 15, 2026");
});

test("adRunLine: ended ads show the run range, year shown once when shared", () => {
  assert.equal(
    adRunLine({
      ad_started_on: "2025-07-10",
      ad_ended_on: "2025-08-22",
      ad_active: false,
      ad_days_running: 43,
    }),
    "Jul 10 – Aug 22, 2025 · 43d",
  );
  // run crosses a year boundary → both dates carry their year
  assert.equal(
    adRunLine({ ad_started_on: "2025-12-20", ad_ended_on: "2026-01-04", ad_active: false, ad_days_running: 15 }),
    "Dec 20, 2025 – Jan 4, 2026 · 15d",
  );
  // ended but no end date recorded → fall back to the start
  assert.equal(
    adRunLine({ ad_started_on: "2025-07-10", ad_active: false, ad_days_running: 6 }),
    "Started Jul 10, 2025 · 6d",
  );
});

test("adRunLine: same-day run shows <1d, never 0d", () => {
  assert.equal(
    adRunLine({ ad_started_on: "2026-02-09", ad_ended_on: "2026-02-09", ad_active: false, ad_days_running: 0 }),
    "Feb 9 – Feb 9, 2026 · <1d",
  );
});

test("adRunLine: unknown start → null (the card renders no run line)", () => {
  assert.equal(adRunLine({ ad_active: true, ad_days_running: 9 }), null);
  assert.equal(adRunLine({}), null);
});
