/**
 * Routing rules for the `/backend/*` proxy (see app/backend/[...path]/route.ts).
 *
 * Pure and dependency-free so it can be tested with `node --test`: the route
 * handler itself pulls in next-auth and cannot be imported from a test. The
 * allowlist is a hand-kept mirror of the Go `/api` group, which is exactly how
 * it went stale once — `backendProxy.test.ts` reads routers.go and fails when
 * the two sets drift again.
 */

// Fail-closed allowlist: the FIRST path segment (the resource) must be one of
// the Go /api group's routes (backend/routers/routers.go). Everything else —
// notably /api/internal/* (key mint, login oracle, key revoke) — is 404'd so a
// signed-in session can never reach it with the server-held internal token. A
// denylist would be miss-prone; this is the closed set.
export const ALLOWED_RESOURCES = new Set([
  "shortlist", // /shortlist, /shortlist/categories
  "gems", // /gems/:id (+ /scaling, /app-metrics)
  "review", // /review/queue, /review/:id/decision
  "preference-queue",
  "enrich", // /enrich/:id (GET status, POST trigger)
  "add-gem", // /add-gem (POST a pasted Ad Library link), /add-gem/:job_id
  "saved-gems", // /saved-gems (+ /:id, /:id/remove)
  "saved-ads", // /saved-ads (+ /:id, /:id/remove)
  "products", // owned product records and link resolution
  "saved-groups", // /saved-groups (+ /:id/update, /:id/remove, /:id/gems)
  "liked-gems", // /liked-gems (built-in Liked collection)
  "ratings", // /ratings (+ /:id)
  "health", // /health/metrics, /health/coverage
  "media", // /media/*key
  "me", // /me (profile), /me/onboarded
  "hunts", // /hunts (+ /:id, /:id/messages, /:id/rounds)
]);

/**
 * Whether the proxy may forward this path.
 *
 * Rejects traversal and malformed segments before matching, so no "../internal"
 * or encoded-slash trick can escape the allowlisted resource, then matches on
 * the FIRST segment only — "internalfoo" won't match "internal", and
 * "gems/123/scaling" resolves to "gems".
 */
export function isProxyablePath(path: string[]): boolean {
  if (path.length === 0) return false;
  if (path.some((seg) => seg === "" || seg === "." || seg === ".." || /[/\\]|%2e|%2f/i.test(seg))) {
    return false;
  }
  const resource = path[0];
  if (resource === "internal") return false;
  return ALLOWED_RESOURCES.has(resource);
}
