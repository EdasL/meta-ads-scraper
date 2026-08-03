import { auth } from "@/lib/auth";
import { NextRequest, NextResponse } from "next/server";

/**
 * Same-origin, session-checked proxy to the Go API. The browser never holds the
 * internal token or an fd_ key: it rides its next-auth session cookie, this
 * handler validates it, then forwards the request with the server-held
 * `AGF_API_TOKEN` + the session's `X-User-Email`.
 *
 * The response body is STREAMED (res.body), never buffered through `text()`, so
 * `/backend/media/*` serves images and video byte-for-byte.
 */
const API = process.env.AGF_API_INTERNAL_URL ?? "http://localhost:8000/api";
const TOKEN = process.env.AGF_API_TOKEN ?? "";

// Response headers worth forwarding to the browser (Content-Type drives <img>/
// <video> decoding; X-Total-Count powers the feed's paging count).
const PASS_THROUGH = ["content-type", "content-length", "cache-control", "x-total-count"];

// Fail-closed allowlist: the FIRST path segment (the resource) must be one of
// the Go /api group's public routes (backend/routers/routers.go). Everything
// else — notably /api/internal/* (key mint, login oracle, key revoke) — is
// 404'd here so a signed-in session can never reach it with the server-held
// internal token. A denylist would be miss-prone; this is the closed set.
const ALLOWED_RESOURCES = new Set([
  "shortlist", // /shortlist, /shortlist/categories
  "gems", // /gems/:id (+ /scaling, /app-metrics)
  "review", // /review/queue, /review/:id/decision
  "preference-queue",
  "enrich", // /enrich/:id (GET status, POST trigger)
  "saved-gems", // /saved-gems (+ /:id, /:id/remove)
  "saved-groups", // /saved-groups (+ /:id/update, /:id/remove, /:id/gems)
  "liked-gems", // /liked-gems (built-in Liked collection)
  "ratings", // /ratings (+ /:id)
  "health", // /health/metrics, /health/coverage
  "media", // /media/*key
  "me", // /me (profile), /me/onboarded
  "hunts", // /hunts (+ /:id, /:id/messages, /:id/rounds)
]);

function notFound() {
  return NextResponse.json({ detail: "not found." }, { status: 404 });
}

async function proxy(req: NextRequest, { params }: { params: Promise<{ path: string[] }> }) {
  const session = await auth();
  const email = session?.user?.email;
  if (!email) return NextResponse.json({ detail: "not signed in." }, { status: 401 });

  const { path } = await params;

  // Reject traversal / malformed segments before matching so no "../internal"
  // or encoded-slash trick can escape the allowlisted resource.
  if (
    path.length === 0 ||
    path.some((seg) => seg === "" || seg === "." || seg === ".." || /[/\\]|%2e|%2f/i.test(seg))
  ) {
    return notFound();
  }

  // Match on the FIRST segment (the resource), not a substring — "internalfoo"
  // won't match "internal", and "gems/123/scaling" resolves to "gems".
  const resource = path[0];
  if (resource === "internal" || !ALLOWED_RESOURCES.has(resource)) {
    return notFound();
  }

  const url = `${API}/${path.join("/")}${req.nextUrl.search}`;

  const hasBody = req.method !== "GET" && req.method !== "HEAD";
  let res: Response;
  try {
    res = await fetch(url, {
      method: req.method,
      headers: {
        Authorization: `Bearer ${TOKEN}`,
        "X-User-Email": email,
        ...(hasBody
          ? { "Content-Type": req.headers.get("content-type") ?? "application/json" }
          : {}),
      },
      body: hasBody ? await req.text() : undefined,
      cache: "no-store",
    });
  } catch {
    // Never leak the token or a stack trace to the browser on a network error.
    return NextResponse.json({ detail: "upstream unavailable." }, { status: 502 });
  }

  const headers = new Headers();
  for (const h of PASS_THROUGH) {
    const v = res.headers.get(h);
    if (v) headers.set(h, v);
  }
  // Stream the upstream body straight through — buffering via text() would
  // corrupt binary media (byte length != string length for images/video).
  return new NextResponse(res.body, { status: res.status, headers });
}

export { proxy as GET, proxy as POST, proxy as DELETE };
