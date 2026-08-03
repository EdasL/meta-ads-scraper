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
  sortGroups,
  bucketByGroup,
  reconcileGroupMerge,
  applyGroupIdRemaps,
  type GroupIdRemap,
  type SavedGroup,
} from "./saved-core.ts";

const row = (over = {}) => ({
  advertiser_id: 1001,
  cohort: "Health & Fitness · US",
  page_name: "SleepWave Labs",
  app_id: 90211,
  app_name: "SleepWave",
  icon_url: "https://cdn.example.com/sleepwave.png",
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
  assert.equal(g.iconUrl, "https://cdn.example.com/sleepwave.png");
  assert.equal(g.stage, "just_saw");
  assert.deepEqual(g.tags, []);
  assert.equal(g.note, "");
  assert.equal(g.scoreSnapshot.gemScore, 88);
});

test("makeSavedGem leaves iconUrl undefined when the row has no icon", () => {
  const g = makeSavedGem(row({ icon_url: undefined }), "2026-06-09T10:00:00Z");
  assert.equal(g.iconUrl, undefined);
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

// --- board groups ---------------------------------------------------------

const group = (id: number, name: string, position: number): SavedGroup => ({ id, name, position });

/** A saved gem with an explicit group + save time, for bucketing tests. */
const gem = (advertiserId: number, groupId: number | null, savedAt: string) => ({
  ...makeSavedGem(row({ advertiser_id: advertiserId }), savedAt),
  groupId,
});

test("makeSavedGem starts a gem ungrouped", () => {
  assert.equal(makeSavedGem(row(), "2026-06-09T10:00:00Z").groupId, null);
});

test("sortGroups orders by position, then id, without mutating the input", () => {
  const input = [group(7, "Later", 2), group(3, "Now", 1), group(2, "Tie", 2), group(9, "First", 0)];
  const sorted = sortGroups(input);
  assert.deepEqual(
    sorted.map((g) => g.id),
    [9, 3, 2, 7],
  );
  // position 2 ties break by id: 2 before 7
  assert.deepEqual(
    input.map((g) => g.id),
    [7, 3, 2, 9],
  );
});

test("bucketByGroup keys every group, even the empty ones", () => {
  const groups = [group(1, "Now", 0), group(2, "Later", 1)];
  const buckets = bucketByGroup([gem(10, 2, "2026-06-01T00:00:00Z")], groups);
  assert.deepEqual([...buckets.keys()], [1, 2]);
  assert.deepEqual(buckets.get(1), []);
  assert.deepEqual(
    buckets.get(2)?.map((g) => g.advertiserId),
    [10],
  );
});

test("bucketByGroup files null and unknown group ids into the first group", () => {
  const groups = [group(1, "Now", 0), group(2, "Later", 1)];
  const buckets = bucketByGroup(
    [
      gem(10, 2, "2026-06-01T00:00:00Z"), // matching group
      gem(11, null, "2026-06-02T00:00:00Z"), // ungrouped
      gem(12, 999, "2026-06-03T00:00:00Z"), // group deleted elsewhere
    ],
    groups,
  );
  assert.deepEqual(
    buckets.get(1)?.map((g) => g.advertiserId),
    [12, 11], // savedAt DESC
  );
  assert.deepEqual(
    buckets.get(2)?.map((g) => g.advertiserId),
    [10],
  );
});

test("bucketByGroup uses the lowest-position group as the fallback, not input order", () => {
  const groups = [group(2, "Later", 1), group(1, "Now", 0)];
  const buckets = bucketByGroup([gem(10, null, "2026-06-01T00:00:00Z")], groups);
  assert.deepEqual(
    buckets.get(1)?.map((g) => g.advertiserId),
    [10],
  );
  assert.deepEqual(buckets.get(2), []);
});

test("bucketByGroup sorts each bucket by savedAt DESC", () => {
  const groups = [group(1, "Now", 0)];
  const buckets = bucketByGroup(
    [
      gem(10, 1, "2026-06-01T00:00:00Z"),
      gem(11, 1, "2026-06-03T00:00:00Z"),
      gem(12, 1, "2026-06-02T00:00:00Z"),
    ],
    groups,
  );
  assert.deepEqual(
    buckets.get(1)?.map((g) => g.advertiserId),
    [11, 12, 10],
  );
});

test("bucketByGroup returns an empty map when there are no groups", () => {
  const buckets = bucketByGroup([gem(10, null, "2026-06-01T00:00:00Z")], []);
  assert.equal(buckets.size, 0);
});

// --- reconciling an optimistic create ---
// The board opens a new column straight into rename mode, so an edit against the
// temp id during the create round-trip is the common case, not the rare one.

const submitted = group(-1, "New group", 4);
const serverRow = group(77, "New group", 4);

test("reconcileGroupMerge takes the server row when nothing changed mid-flight", () => {
  const { row, needsPersist } = reconcileGroupMerge(submitted, submitted, serverRow);
  assert.deepEqual(row, { id: 77, name: "New group", position: 4 });
  assert.equal(needsPersist, false);
});

test("reconcileGroupMerge keeps a mid-flight rename and flags it for persist", () => {
  const local = { ...submitted, name: "Paid social" };
  const { row, needsPersist } = reconcileGroupMerge(local, submitted, serverRow);
  assert.equal(row.id, 77); // real id, always
  assert.equal(row.name, "Paid social"); // the user's typing survives
  assert.equal(row.position, 4); // untouched → server's value
  assert.equal(needsPersist, true);
});

test("reconcileGroupMerge keeps a mid-flight move and flags it for persist", () => {
  const local = { ...submitted, position: 0 };
  const { row, needsPersist } = reconcileGroupMerge(local, submitted, serverRow);
  assert.equal(row.position, 0);
  assert.equal(row.name, "New group");
  assert.equal(needsPersist, true);
});

test("reconcileGroupMerge lets the server win on fields the user never touched", () => {
  // The backend trimmed the name and appended at a different slot than guessed.
  const server = group(77, "New group", 9);
  const { row, needsPersist } = reconcileGroupMerge(submitted, submitted, server);
  assert.equal(row.position, 9);
  assert.equal(row.name, "New group");
  assert.equal(needsPersist, false);
});

test("reconcileGroupMerge skips the persist when the local edit matches the server", () => {
  // Renamed mid-flight to exactly what the backend already stored.
  const server = group(77, "Paid social", 4);
  const local = { ...submitted, name: "Paid social" };
  const { row, needsPersist } = reconcileGroupMerge(local, submitted, server);
  assert.equal(row.name, "Paid social");
  assert.equal(needsPersist, false);
});

test("reconcileGroupMerge carries both edits at once", () => {
  const local = { ...submitted, name: "Paid social", position: 0 };
  const { row, needsPersist } = reconcileGroupMerge(local, submitted, serverRow);
  assert.deepEqual(row, { id: 77, name: "Paid social", position: 0 });
  assert.equal(needsPersist, true);
});

const remap = (tempId: number, realId: number, seq: number): GroupIdRemap => ({ tempId, realId, seq });

test("applyGroupIdRemaps attributes each same-batch create to its own column", () => {
  // Two creates in flight; the user is editing the SECOND one (-2). Both
  // responses land in one batch. -1 → 71 and -2 → 72 must each keep their own
  // identity: editing follows -2 to 72, never 71.
  const log = [remap(-1, 71, 1), remap(-2, 72, 2)];
  const out = applyGroupIdRemaps(log, 0, new Map(), -2);
  assert.equal(out.editingId, 72);
  assert.equal(out.stableKeys.get(71), -1);
  assert.equal(out.stableKeys.get(72), -2);
  assert.equal(out.lastSeq, 2);
});

test("applyGroupIdRemaps applies each entry once via the seq cursor", () => {
  const log = [remap(-1, 71, 1), remap(-2, 72, 2)];
  const first = applyGroupIdRemaps(log, 0, new Map(), null);
  // Re-reading the same log with the advanced cursor is a no-op — the map
  // reference comes back unchanged and the cursor stays put.
  const second = applyGroupIdRemaps(log, first.lastSeq, first.stableKeys, null);
  assert.equal(second.stableKeys, first.stableKeys);
  assert.equal(second.lastSeq, first.lastSeq);
});

test("applyGroupIdRemaps chains an existing stable key through the swap", () => {
  // The temp column already carried a stable key (from a previous life); the
  // real id inherits that original key, keeping the DOM node's identity.
  const prior: ReadonlyMap<number, number> = new Map([[-3, -1]]);
  const out = applyGroupIdRemaps([remap(-3, 90, 5)], 4, prior, null);
  assert.equal(out.stableKeys.get(90), -1);
});

test("applyGroupIdRemaps leaves an unrelated editing session alone", () => {
  const out = applyGroupIdRemaps([remap(-1, 71, 1)], 0, new Map(), 55);
  assert.equal(out.editingId, 55);
});
