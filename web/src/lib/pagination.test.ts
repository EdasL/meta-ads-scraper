/**
 * Pure-logic tests for lib/pagination.ts. Run with
 *   node --test src/lib/pagination.test.ts
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { clampPage, pageItems } from "./pagination.ts";

test("no pages / single page", () => {
  assert.deepEqual(pageItems(1, 0), []);
  assert.deepEqual(pageItems(1, 1), [1]);
});

test("few pages → all visible, no gaps", () => {
  assert.deepEqual(pageItems(1, 3), [1, 2, 3]);
  assert.deepEqual(pageItems(2, 4), [1, 2, 3, 4]);
});

test("middle page elides both sides", () => {
  assert.deepEqual(pageItems(6, 12), [1, "gap", 5, 6, 7, "gap", 12]);
});

test("near the edges only the far side elides", () => {
  assert.deepEqual(pageItems(1, 12), [1, 2, "gap", 12]);
  assert.deepEqual(pageItems(12, 12), [1, "gap", 11, 12]);
});

test("a one-page gap shows the page instead of an ellipsis", () => {
  // window ends at 4, last is 6 → 5 fills the gap.
  assert.deepEqual(pageItems(3, 6), [1, 2, 3, 4, 5, 6]);
});

test("clampPage keeps the page on an existing page", () => {
  assert.equal(clampPage(5, 30, 24), 2, "30 rows = 2 pages");
  assert.equal(clampPage(0, 30, 24), 1);
  assert.equal(clampPage(1, 0, 24), 1, "empty result set still has page 1");
});
