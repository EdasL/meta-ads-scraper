/**
 * Pure-helper tests for lib/hunt.ts. Zero-dependency: run with
 *   node --test src/lib/hunt.test.ts
 * (Node 22 strips the TS types at load; this file is excluded from tsc/Next via
 * tsconfig `exclude`, so the `.ts`-extension import below is fine.)
 *
 * Fixtures mirror the real wire shapes confirmed against
 * backend/models/hunt.go + backend/controllers/hunt_controller.go, so the
 * helpers are pinned to the committed contract (server normalizeHuntTerms:
 * trim + lowercase + dedupe, drop empty/over-80, cap 15).
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { parseProposal, editTerms, huntIsActive, latestEvents, formatSettings, splitByRelevance } from "./hunt.ts";
import type { HuntResult } from "./api.ts";

// --- formatSettings ----------------------------------------------------------
// Server defaults are page_cap 40 / patience 6 / listing_pages 10 / min_pages 10;
// formatSettings shows only keys that differ, labelled depth/patience/listing/min.

test("formatSettings hides keys equal to the defaults", () => {
  assert.equal(formatSettings({ page_cap: 40, patience: 6, listing_pages: 10, min_pages: 10 }), "");
});

test("formatSettings is empty for an empty object or undefined", () => {
  assert.equal(formatSettings({}), "");
  assert.equal(formatSettings(undefined), "");
});

test("formatSettings shows a single non-default override", () => {
  assert.equal(formatSettings({ patience: 8 }), "patience 8");
  assert.equal(formatSettings({ page_cap: 40, patience: 8, listing_pages: 10 }), "patience 8");
});

test("formatSettings shows all four non-default keys in depth·patience·listing·min order", () => {
  assert.equal(
    formatSettings({ page_cap: 50, patience: 8, listing_pages: 20, min_pages: 4 }),
    "depth 50 · patience 8 · listing 20 · min 4",
  );
});

test("formatSettings hides min_pages at its default (10) and shows an override", () => {
  assert.equal(formatSettings({ min_pages: 10 }), "");
  assert.equal(formatSettings({ min_pages: 3 }), "min 3");
});

test("formatSettings shows out-of-range values as-is (server already clamped)", () => {
  assert.equal(formatSettings({ page_cap: 200 }), "depth 200");
  assert.equal(formatSettings({ patience: 99, listing_pages: 99 }), "patience 99 · listing 99");
});

test("parseProposal passes min_pages through in settings", () => {
  assert.deepEqual(
    parseProposal({ terms: ["a"], competitor_apps: [], settings: { min_pages: 4 } }),
    { terms: ["a"], competitor_apps: [], settings: { min_pages: 4 } },
  );
});

// --- parseProposal -----------------------------------------------------------

test("parseProposal accepts {reply, terms, competitor_apps} and drops reply", () => {
  const got = parseProposal({
    reply: "Here are some angles.",
    terms: ["sleep tracker", "lucid dreaming"],
    competitor_apps: ["Calm"],
  });
  assert.deepEqual(got, {
    terms: ["sleep tracker", "lucid dreaming"],
    competitor_apps: ["Calm"],
  });
});

test("parseProposal accepts empty arrays", () => {
  assert.deepEqual(parseProposal({ terms: [], competitor_apps: [] }), {
    terms: [],
    competitor_apps: [],
  });
});

test("parseProposal rejects null", () => {
  assert.equal(parseProposal(null), null);
});

test("parseProposal rejects a non-object", () => {
  assert.equal(parseProposal("nope"), null);
  assert.equal(parseProposal(42), null);
  assert.equal(parseProposal(undefined), null);
});

test("parseProposal rejects missing / non-array fields", () => {
  assert.equal(parseProposal({}), null);
  assert.equal(parseProposal({ terms: ["a"] }), null); // competitor_apps missing
  assert.equal(parseProposal({ competitor_apps: [] }), null); // terms missing
  assert.equal(parseProposal({ terms: "a", competitor_apps: [] }), null); // terms not array
});

test("parseProposal drops non-string array elements", () => {
  assert.deepEqual(parseProposal({ terms: ["ok", 5, null, "two"], competitor_apps: [true, "App"] }), {
    terms: ["ok", "two"],
    competitor_apps: ["App"],
  });
});

test("parseProposal passes a settings object through", () => {
  assert.deepEqual(
    parseProposal({ terms: ["a"], competitor_apps: [], settings: { page_cap: 50, patience: 8 } }),
    { terms: ["a"], competitor_apps: [], settings: { page_cap: 50, patience: 8 } },
  );
});

test("parseProposal omits settings for the legacy shape without it", () => {
  assert.deepEqual(parseProposal({ terms: ["a"], competitor_apps: ["App"] }), {
    terms: ["a"],
    competitor_apps: ["App"],
  });
});

test("parseProposal omits an empty / non-object settings", () => {
  assert.deepEqual(parseProposal({ terms: ["a"], competitor_apps: [], settings: {} }), {
    terms: ["a"],
    competitor_apps: [],
  });
  assert.deepEqual(parseProposal({ terms: ["a"], competitor_apps: [], settings: "nope" }), {
    terms: ["a"],
    competitor_apps: [],
  });
});

// --- editTerms ---------------------------------------------------------------

test("editTerms add trims + lowercases", () => {
  assert.deepEqual(editTerms(["sleep"], { add: "  Lucid Dreaming  " }), ["sleep", "lucid dreaming"]);
});

test("editTerms add dedupes (case-insensitive, no-op)", () => {
  assert.deepEqual(editTerms(["sleep tracker"], { add: "Sleep Tracker" }), ["sleep tracker"]);
});

test("editTerms normalizes + dedupes the existing list too", () => {
  assert.deepEqual(editTerms(["Sleep", "sleep", "  DREAMS "], {}), ["sleep", "dreams"]);
});

test("editTerms add is refused beyond 15", () => {
  const full = Array.from({ length: 15 }, (_, i) => `term${i}`);
  assert.equal(full.length, 15);
  const got = editTerms(full, { add: "sixteenth" });
  assert.deepEqual(got, full);
  assert.equal(got.length, 15);
});

test("editTerms add ignores empty / over-80-char terms", () => {
  assert.deepEqual(editTerms(["sleep"], { add: "   " }), ["sleep"]);
  const long = "x".repeat(81);
  assert.deepEqual(editTerms(["sleep"], { add: long }), ["sleep"]);
});

test("editTerms remove (case-insensitive)", () => {
  assert.deepEqual(editTerms(["sleep", "dreams"], { remove: "Sleep" }), ["dreams"]);
});

test("editTerms remove of an absent term is a no-op", () => {
  assert.deepEqual(editTerms(["sleep"], { remove: "meditation" }), ["sleep"]);
});

// --- huntIsActive ------------------------------------------------------------

const round = (status) => ({
  id: 1,
  message_id: null,
  terms: [],
  competitor_apps: [],
  status,
  progress: {},
  error_text: null,
  created_at: "",
  finished_at: null,
});

test("huntIsActive true when any round is queued or running", () => {
  assert.equal(huntIsActive([round("done"), round("queued")]), true);
  assert.equal(huntIsActive([round("running")]), true);
});

test("huntIsActive false when all rounds are terminal", () => {
  assert.equal(huntIsActive([round("done"), round("error")]), false);
  assert.equal(huntIsActive([]), false);
});

// --- latestEvents ------------------------------------------------------------

test("latestEvents returns events newest-first", () => {
  const r = round("running");
  r.progress = {
    events: [
      { t: "1", msg: "started" },
      { t: "2", msg: "midway" },
      { t: "3", msg: "done" },
    ],
  };
  assert.deepEqual(latestEvents(r), [
    { t: "3", msg: "done" },
    { t: "2", msg: "midway" },
    { t: "1", msg: "started" },
  ]);
});

test("latestEvents caps to n newest", () => {
  const r = round("running");
  r.progress = { events: [{ t: "1", msg: "a" }, { t: "2", msg: "b" }, { t: "3", msg: "c" }] };
  assert.deepEqual(latestEvents(r, 2), [
    { t: "3", msg: "c" },
    { t: "2", msg: "b" },
  ]);
});

test("latestEvents returns [] for empty progress", () => {
  assert.deepEqual(latestEvents(round("queued")), []);
  const r = round("running");
  r.progress = { found: 0 };
  assert.deepEqual(latestEvents(r), []);
});

// --- splitByRelevance --------------------------------------------------------
// Server tags each result relevant / off_target / null; null (unjudged) and
// relevant stay on target, only off_target moves to the collapsed group.

test("splitByRelevance keeps null/relevant on target, moves off_target", () => {
  const mk = (id: number, relevance: "relevant" | "off_target" | null) =>
    ({ id, relevance } as unknown as HuntResult);
  const { onTarget, offTarget } = splitByRelevance([mk(1, null), mk(2, "relevant"), mk(3, "off_target")]);
  assert.deepEqual(onTarget.map((r) => r.id), [1, 2]);
  assert.deepEqual(offTarget.map((r) => r.id), [3]);
});

// --- rollupByPage ------------------------------------------------------------

import { rollupByPage } from "./hunt.ts";

function hr(over: Partial<import("./api").HuntResult>): import("./api").HuntResult {
  return {
    id: 1, round_id: 1, page_id: "P", page_name: "Page", store: "web",
    store_id: "x", link_url: "https://x/", source_term: "t", was_new: false,
    triage: "kept", kept_advertiser_id: null, gem_score: null, page: null,
    app_name: null, icon_url: null, tagline: null, cohort: null, saved: false,
    rating: null, relevance: null, relevance_note: null, ...over,
  };
}

test("rollupByPage collapses a page's rows and unions its stores", () => {
  const rows = [
    hr({ id: 1, page_id: "A", store: "ios", link_url: "https://ios/a", kept_advertiser_id: 42, gem_score: 41, app_name: "Coopah" }),
    hr({ id: 2, page_id: "A", store: "android", link_url: "https://and/a" }),
    hr({ id: 3, page_id: "A", store: "web", link_url: "https://web/a" }),
    hr({ id: 4, page_id: "B", store: "web", link_url: "https://web/b" }),
  ];
  const out = rollupByPage(rows);
  assert.equal(out.length, 2, "two pages -> two cards");
  const a = out[0];
  assert.equal(a.page_id, "A");
  assert.equal(a.fold_count, 3);
  assert.deepEqual(a.stores.map((s) => s.store), ["ios", "android", "web"]);
  assert.equal(a.rep.id, 1, "representative is the resolved row");
  assert.equal(a.rep.kept_advertiser_id, 42);
  assert.equal(out[1].fold_count, 1);
});

test("rollupByPage picks first row when none resolved, and keeps kept triage", () => {
  const rows = [
    hr({ id: 1, page_id: "A", store: "web", triage: "vetoed", kept_advertiser_id: null }),
    hr({ id: 2, page_id: "A", store: "ios", triage: "kept", kept_advertiser_id: null }),
  ];
  const out = rollupByPage(rows);
  assert.equal(out.length, 1);
  assert.equal(out[0].rep.id, 1, "no resolved row -> first row is representative");
  assert.equal(out[0].triage, "kept", "any kept -> group triage is kept");
});
