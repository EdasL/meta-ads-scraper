import type {
  GemDossier,
  ReviewDecision,
  ReviewQueueRow,
  ScalingPoint,
  ShortlistQuery,
  ShortlistRow,
} from "@/types/contracts";
import type {
  ApiAppMetricPoint,
  ApiGemDossier,
  ApiReviewQueueRow,
  ApiSavedGem,
  ApiSavedGemIn,
  ApiScalingPoint,
  ApiShortlistRow,
} from "@/types/api";
import {
  adaptDossier,
  adaptReviewRow,
  adaptScalingPoint,
  adaptShortlistRow,
} from "./adapters";

/**
 * Live-only data client for the Go read-only API. Every call hits the backend;
 * the adapters translate the snake_case/nullable wire shapes into the flat UI
 * contract. Configure the base via NEXT_PUBLIC_API_URL (must end in `/api`).
 */
const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api";

/** Builds the /api/media/{key} proxy URL for an S3 object key. */
const mediaUrl = (key: string) => `${API_BASE}/media/${key}`;

async function live<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) throw new Error(`API ${res.status}: ${res.statusText}`);
  return (await res.json()) as T;
}

/** Like `live` but maps a 404 to null (a missing gem is not an error). */
async function liveOrNull<T>(path: string): Promise<T | null> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
  });
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`API ${res.status}: ${res.statusText}`);
  return (await res.json()) as T;
}

export async function getShortlist(query: ShortlistQuery = {}): Promise<ShortlistRow[]> {
  const params = new URLSearchParams();
  if (query.cohort) params.set("cohort", query.cohort);
  if (query.min_score != null) params.set("min_score", String(query.min_score));
  const rows = await live<ApiShortlistRow[]>(`/shortlist?${params}`);
  return rows.map((r) => adaptShortlistRow(r, mediaUrl));
}

export async function getGem(advertiserId: number): Promise<GemDossier | null> {
  const dossier = await liveOrNull<ApiGemDossier>(`/gems/${advertiserId}`);
  if (!dossier) return null;
  // The dossier page reads `gem.scaling` / `gem.app_metrics` inline; both are
  // separate endpoints server-side, so fetch them in parallel and merge into
  // the flat shape. app-metrics is newer — ANY failure (404 on older API
  // builds, but also 5xx/network errors) degrades to an empty growth chart.
  const [scaling, appMetrics] = await Promise.all([
    live<ApiScalingPoint[]>(`/gems/${advertiserId}/scaling`),
    live<ApiAppMetricPoint[]>(`/gems/${advertiserId}/app-metrics`).catch(
      (err) => {
        console.warn(
          `app-metrics fetch failed for advertiser ${advertiserId}; rendering empty chart`,
          err,
        );
        return [] as ApiAppMetricPoint[];
      },
    ),
  ]);
  return adaptDossier(dossier, scaling, appMetrics ?? [], mediaUrl);
}

export async function getScaling(advertiserId: number): Promise<ScalingPoint[]> {
  const points = await live<ApiScalingPoint[]>(`/gems/${advertiserId}/scaling`);
  return points.map(adaptScalingPoint);
}

export async function getReviewQueue(): Promise<ReviewQueueRow[]> {
  const rows = await live<ApiReviewQueueRow[]>(`/review/queue`);
  return rows.map(adaptReviewRow);
}

export async function postReviewDecision(
  advertiserId: number,
  decision: ReviewDecision,
): Promise<{ ok: true }> {
  await live<{ status: string }>(`/review/${advertiserId}/decision`, {
    method: "POST",
    body: JSON.stringify(decision),
  });
  return { ok: true } as const;
}

// --- Saved gems (server-side watchlist) ---
// These hit the Go backend directly: the watchlist is fully backend-persisted.

export async function getSavedGems(userEmail: string): Promise<ApiSavedGem[]> {
  return live<ApiSavedGem[]>(`/saved-gems?user_email=${encodeURIComponent(userEmail)}`);
}

export async function upsertSavedGem(advertiserId: number, body: ApiSavedGemIn): Promise<void> {
  await live(`/saved-gems/${advertiserId}`, { method: "POST", body: JSON.stringify(body) });
}

export async function removeSavedGem(advertiserId: number, userEmail: string): Promise<void> {
  await live(`/saved-gems/${advertiserId}/remove`, {
    method: "POST",
    body: JSON.stringify({ user_email: userEmail }),
  });
}
