/**
 * Pure-logic tests for lib/onboarding.ts checklistSteps. Run with
 *   node --test src/lib/onboarding.test.ts
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { checklistSteps } from "./onboarding.ts";

test("checklistSteps: empty flags + no saves => nothing done", () => {
  const { steps, allDone } = checklistSteps({}, { savedCount: 0, hasNote: false });
  assert.equal(steps.length, 4);
  assert.deepEqual(steps.map((s) => s.done), [false, false, false, false]);
  assert.equal(allDone, false);
});

test("checklistSteps: flags + save + note => all done", () => {
  const { steps, allDone } = checklistSteps(
    { visitedGem: true, expandedWhy: true },
    { savedCount: 2, hasNote: true },
  );
  assert.deepEqual(steps.map((s) => s.done), [true, true, true, true]);
  assert.equal(allDone, true);
});

test("checklistSteps: save without note keeps note step open", () => {
  const { steps, allDone } = checklistSteps(
    { visitedGem: true, expandedWhy: true },
    { savedCount: 1, hasNote: false },
  );
  assert.equal(steps.find((s) => s.id === "save")?.done, true);
  assert.equal(steps.find((s) => s.id === "note")?.done, false);
  assert.equal(allDone, false);
});
