/**
 * Pure-logic tests for lib/query.ts. Run with
 *   node --test src/lib/query.test.ts
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { shortlistParams } from "./query.ts";

test("empty query → empty params", () => {
  assert.equal(shortlistParams({}).toString(), "");
});

test("defaults are omitted", () => {
  assert.equal(
    shortlistParams({ sort: "gemscore", min_score: 0, min_ad_age: 0, offset: 0, rising: false, has_funnel: false }).toString(),
    "",
  );
});

test("every active axis is serialized", () => {
  const params = shortlistParams({
    category: "Health & Fitness",
    q: "walk",
    min_score: 40,
    rising: true,
    has_funnel: true,
    min_ad_age: 21,
    sort: "newest",
    limit: 24,
    offset: 48,
  });
  assert.equal(params.get("category"), "Health & Fitness");
  assert.equal(params.get("q"), "walk");
  assert.equal(params.get("min_score"), "40");
  assert.equal(params.get("rising"), "true");
  assert.equal(params.get("has_funnel"), "true");
  assert.equal(params.get("min_ad_age"), "21");
  assert.equal(params.get("sort"), "newest");
  assert.equal(params.get("limit"), "24");
  assert.equal(params.get("offset"), "48");
});

test("dir matching the sort key's natural direction is omitted", () => {
  assert.equal(shortlistParams({ sort: "gemscore", dir: "desc" }).toString(), "");
  assert.equal(shortlistParams({ sort: "newest", dir: "asc" }).toString(), "sort=newest");
});

test("reversed dir is serialized, including against the default sort", () => {
  assert.equal(shortlistParams({ dir: "asc" }).get("dir"), "asc");
  const params = shortlistParams({ sort: "newest", dir: "desc" });
  assert.equal(params.get("sort"), "newest");
  assert.equal(params.get("dir"), "desc");
});

test("category with ampersand survives URL encoding round-trip", () => {
  const round = new URLSearchParams(shortlistParams({ category: "Health & Fitness" }).toString());
  assert.equal(round.get("category"), "Health & Fitness");
});

test("limit 0 is still sent (explicit empty page), offset 0 is not", () => {
  const params = shortlistParams({ limit: 0, offset: 0 });
  assert.equal(params.get("limit"), "0");
  assert.equal(params.get("offset"), null);
});
