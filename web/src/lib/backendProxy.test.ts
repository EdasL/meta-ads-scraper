/**
 * Tests for the /backend proxy's routing rules. Zero-dependency: run with
 *   node --test src/lib/backendProxy.test.ts
 *
 * The drift test is the point of this file. The allowlist duplicates the Go
 * router's resource set by hand, and on 2026-08-06 the add-gem feature shipped
 * an API route, an API client and a UI without touching it — so every paste in
 * the browser came back `{"detail":"not found."}` from this proxy while the API
 * itself worked. Reading routers.go here means the next new resource fails a
 * test instead of failing in the founder's browser.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { ALLOWED_RESOURCES, isProxyablePath } from "./backendProxy.ts";

const routersGo = fileURLToPath(
  new URL("../../../backend/routers/routers.go", import.meta.url),
);

/** First path segment of every route registered on the Go `/api` group. */
function apiResourcesFromRouter(source: string): Set<string> {
  const found = new Set<string>();
  // api.GET("/gems/:advertiser_id/scaling", ...) -> "gems"
  // Only `api.` matches: the /api/internal group is registered on `internal.`
  // and must stay unreachable through this proxy.
  for (const m of source.matchAll(/\bapi\.(?:GET|POST|PUT|PATCH|DELETE)\("\/([^/"]+)/g)) {
    found.add(m[1]);
  }
  return found;
}

test("every Go /api resource is proxyable", () => {
  const resources = apiResourcesFromRouter(readFileSync(routersGo, "utf8"));
  assert.ok(resources.size > 10, `parsed too few routes (${resources.size}) — regex is stale`);
  const missing = [...resources].filter((r) => !ALLOWED_RESOURCES.has(r)).sort();
  assert.deepEqual(
    missing,
    [],
    `these API resources are unreachable through /backend: ${missing.join(", ")}`,
  );
});

test("the allowlist carries nothing the Go router does not serve", () => {
  const resources = apiResourcesFromRouter(readFileSync(routersGo, "utf8"));
  const stale = [...ALLOWED_RESOURCES].filter((r) => !resources.has(r)).sort();
  assert.deepEqual(stale, [], `allowlisted but no longer routed: ${stale.join(", ")}`);
});

test("add-gem is proxyable, both the POST and the status poll", () => {
  assert.equal(isProxyablePath(["add-gem"]), true);
  assert.equal(isProxyablePath(["add-gem", "1234"]), true);
});

test("the internal key-minting group stays unreachable", () => {
  assert.equal(isProxyablePath(["internal"]), false);
  assert.equal(isProxyablePath(["internal", "keys"]), false);
  // Prefix lookalikes must not slip past the first-segment match.
  assert.equal(isProxyablePath(["internalfoo"]), false);
});

test("traversal and malformed segments are refused", () => {
  assert.equal(isProxyablePath([]), false);
  assert.equal(isProxyablePath([""]), false);
  assert.equal(isProxyablePath(["gems", ".."]), false);
  assert.equal(isProxyablePath(["gems", "%2e%2e"]), false);
  assert.equal(isProxyablePath(["gems", "a/b"]), false);
});
