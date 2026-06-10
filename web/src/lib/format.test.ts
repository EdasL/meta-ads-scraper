/**
 * Pure-logic tests for lib/format.ts. Run with
 *   node --test src/lib/format.test.ts
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
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
