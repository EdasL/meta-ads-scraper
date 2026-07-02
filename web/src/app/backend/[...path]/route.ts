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

async function proxy(req: NextRequest, { params }: { params: Promise<{ path: string[] }> }) {
  const session = await auth();
  const email = session?.user?.email;
  if (!email) return NextResponse.json({ detail: "not signed in." }, { status: 401 });

  const { path } = await params;
  const url = `${API}/${path.join("/")}${req.nextUrl.search}`;

  const hasBody = req.method !== "GET" && req.method !== "HEAD";
  const res = await fetch(url, {
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
