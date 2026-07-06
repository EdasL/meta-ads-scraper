/**
 * Pure-logic tests for lib/reviewBuffer.ts. Run with
 *   node --test src/lib/reviewBuffer.test.ts
 */
import { test, mock } from "node:test";
import assert from "node:assert/strict";
import { mergeRefill, refillExclude, RefillScheduler, RECENT_RATED_SENT } from "./reviewBuffer.ts";

const c = (id: number) => ({ advertiser_id: id });

test("mergeRefill keeps current + next card, replaces the unseen tail", () => {
  const cards = [c(1), c(2), c(3), c(4), c(5)];
  const merged = mergeRefill(cards, 1, [c(9), c(8)]);
  // idx=1: keep cards[0..2] (passed card 1, current 2, next 3), replace 4,5.
  assert.deepEqual(merged.map((x) => x.advertiser_id), [1, 2, 3, 9, 8]);
});

test("mergeRefill drops refill rows already kept (no duplicates)", () => {
  const cards = [c(1), c(2), c(3)];
  const merged = mergeRefill(cards, 0, [c(2), c(7)]);
  assert.deepEqual(merged.map((x) => x.advertiser_id), [1, 2, 7]);
});

test("mergeRefill with an empty refill leaves the kept head only", () => {
  const cards = [c(1), c(2), c(3), c(4)];
  assert.deepEqual(mergeRefill(cards, 0, []).map((x) => x.advertiser_id), [1, 2]);
});

test("refillExclude sends unseen buffer ids plus recent rated ids", () => {
  const cards = [c(1), c(2), c(3)];
  const got = refillExclude(cards, 1, [10, 11]);
  assert.deepEqual([...got].sort((a, b) => a - b), [2, 3, 10, 11]);
});

test("refillExclude caps rated ids to the most recent window", () => {
  const rated = Array.from({ length: RECENT_RATED_SENT + 15 }, (_, i) => i + 1);
  const got = refillExclude([], 0, rated);
  assert.equal(got.length, RECENT_RATED_SENT);
  assert.ok(got.includes(rated[rated.length - 1]), "newest rated id included");
  assert.ok(!got.includes(rated[0]), "oldest beyond the window dropped");
});

test("RefillScheduler debounces bursts into one run", async () => {
  mock.timers.enable({ apis: ["setTimeout"] });
  let runs = 0;
  const s = new RefillScheduler(async () => {
    runs += 1;
  }, 300);
  s.request();
  s.request();
  s.request();
  mock.timers.tick(299);
  assert.equal(runs, 0, "nothing fires before the debounce");
  mock.timers.tick(1);
  await Promise.resolve(); // let the run's microtask settle
  assert.equal(runs, 1, "one run for the whole burst");
  mock.timers.reset();
});

test("RefillScheduler is single-flight and reruns once after a busy run", async () => {
  mock.timers.enable({ apis: ["setTimeout"] });
  let release: () => void = () => {};
  let runs = 0;
  const s = new RefillScheduler(() => {
    runs += 1;
    return new Promise<void>((res) => {
      release = res;
    });
  }, 300);
  s.request();
  mock.timers.tick(300);
  assert.equal(runs, 1);
  // Two more requests while the first run is still in flight…
  s.request();
  mock.timers.tick(300);
  s.request();
  mock.timers.tick(300);
  assert.equal(runs, 1, "no concurrent run");
  release(); // finish run 1
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(runs, 2, "exactly one catch-up run");
  release();
  mock.timers.reset();
});

test("RefillScheduler swallows run errors and stays usable", async () => {
  mock.timers.enable({ apis: ["setTimeout"] });
  let runs = 0;
  const s = new RefillScheduler(async () => {
    runs += 1;
    throw new Error("network");
  }, 300);
  s.request();
  mock.timers.tick(300);
  await Promise.resolve();
  await Promise.resolve();
  s.request();
  mock.timers.tick(300);
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(runs, 2, "a failed run does not wedge the scheduler");
  mock.timers.reset();
});

test("dispose cancels a pending debounce", () => {
  mock.timers.enable({ apis: ["setTimeout"] });
  let runs = 0;
  const s = new RefillScheduler(async () => {
    runs += 1;
  }, 300);
  s.request();
  s.dispose();
  mock.timers.tick(1000);
  assert.equal(runs, 0);
  mock.timers.reset();
});
