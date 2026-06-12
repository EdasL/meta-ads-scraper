/**
 * Pure-logic tests for lib/returnTo.ts. Zero-dependency: run with
 *   node --test src/lib/returnTo.test.ts
 * (Node 22 strips TS types at load; excluded from tsc/Next via tsconfig.)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { saveReturnTo, loadReturnTo, clearReturnTo, type StorageLike } from "./returnTo.ts";

function fakeStore(): StorageLike & { map: Map<string, string> } {
  const map = new Map<string, string>();
  return {
    map,
    getItem: (k) => map.get(k) ?? null,
    setItem: (k, v) => void map.set(k, v),
    removeItem: (k) => void map.delete(k),
  };
}

test("save then load round-trips url and scroll", () => {
  const store = fakeStore();
  saveReturnTo({ url: "/discover?page=3&sort=fastest", scrollY: 1240 }, store);
  assert.deepEqual(loadReturnTo(store), { url: "/discover?page=3&sort=fastest", scrollY: 1240 });
});

test("load returns null when nothing was saved", () => {
  assert.equal(loadReturnTo(fakeStore()), null);
});

test("clear removes the entry", () => {
  const store = fakeStore();
  saveReturnTo({ url: "/discover", scrollY: 0 }, store);
  clearReturnTo(store);
  assert.equal(loadReturnTo(store), null);
});

test("load rejects URLs outside the Discover feed", () => {
  const store = fakeStore();
  store.setItem("fd.discover.return", JSON.stringify({ url: "https://evil.example/", scrollY: 0 }));
  assert.equal(loadReturnTo(store), null);
  store.setItem("fd.discover.return", JSON.stringify({ url: "/saved", scrollY: 0 }));
  assert.equal(loadReturnTo(store), null);
});

test("load survives garbage payloads", () => {
  const store = fakeStore();
  for (const raw of ["not json", "42", "null", JSON.stringify({ scrollY: 9 })]) {
    store.setItem("fd.discover.return", raw);
    assert.equal(loadReturnTo(store), null, `raw=${raw}`);
  }
});

test("load clamps a bad scrollY to 0 but keeps the url", () => {
  const store = fakeStore();
  store.setItem("fd.discover.return", JSON.stringify({ url: "/discover?page=2", scrollY: "high" }));
  assert.deepEqual(loadReturnTo(store), { url: "/discover?page=2", scrollY: 0 });
  store.setItem("fd.discover.return", JSON.stringify({ url: "/discover", scrollY: -5 }));
  assert.deepEqual(loadReturnTo(store), { url: "/discover", scrollY: 0 });
});

test("save never throws when the store is full", () => {
  const store: StorageLike = {
    getItem: () => null,
    setItem: () => {
      throw new Error("QuotaExceededError");
    },
    removeItem: () => {},
  };
  assert.doesNotThrow(() => saveReturnTo({ url: "/discover", scrollY: 10 }, store));
});
