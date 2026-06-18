import type {
  CategoryCount,
  GemDossier,
  PreferenceCard,
  ReviewDecision,
  ReviewQueueRow,
  ScalingPoint,
  ShortlistPage,
  ShortlistQuery,
} from "@/types/contracts";
import { shortlistParams } from "./query";
import type {
  ApiAppMetricPoint,
  ApiGemDossier,
  ApiPreferenceCardRow,
  ApiReviewQueueRow,
  ApiSavedGem,
  ApiSavedGemIn,
  ApiScalingPoint,
  ApiShortlistRow,
} from "@/types/api";
import {
  adaptDossier,
  adaptPreferenceCard,
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

// Optional bearer token for the token-gated write endpoints (enrich, review,
// saved-gems). The Go middleware no-ops when AGF_API_TOKEN is unset, so this
// is only needed when the API is configured to require it.
const API_TOKEN = process.env.NEXT_PUBLIC_API_TOKEN;

/** Builds the /api/media/{key} proxy URL for an S3 object key. */
const mediaUrl = (key: string) => `${API_BASE}/media/${key}`;

async function live<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    // no-store: the feed must reflect the live DB. Without it this Next version
    // caches the GET and the UI shows a stale count after a re-score (the
    // "stuck at ~300" symptom).
    cache: "no-store",
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(API_TOKEN ? { Authorization: `Bearer ${API_TOKEN}` } : {}),
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) throw new Error(`API ${res.status}: ${res.statusText}`);
  return (await res.json()) as T;
}

/** Like `live` but maps a 404 to null (a missing gem is not an error). */
async function liveOrNull<T>(path: string): Promise<T | null> {
  const res = await fetch(`${API_BASE}${path}`, {
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
  });
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`API ${res.status}: ${res.statusText}`);
  return (await res.json()) as T;
}

export async function getShortlist(
  query: ShortlistQuery = {},
  signal?: AbortSignal,
): Promise<ShortlistPage> {
  // Raw fetch (not `live`) because the all-pages total rides the
  // X-Total-Count response header; the body stays the bare row array.
  // Callers page with limit/offset — the old fetch-the-full-pool limit=1000
  // workaround is gone.
  const res = await fetch(`${API_BASE}/shortlist?${shortlistParams(query)}`, {
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    signal,
  });
  if (!res.ok) throw new Error(`API ${res.status}: ${res.statusText}`);
  const rows = ((await res.json()) as ApiShortlistRow[]).map((r) => adaptShortlistRow(r, mediaUrl));
  const header = Number(res.headers.get("X-Total-Count"));
  return { rows, total: Number.isFinite(header) && header >= rows.length ? header : rows.length };
}

/** The feed's real categories (store genres) with gem counts under `query`'s
 * filters — pass the active filter set (minus category) so each count matches
 * what picking that category would yield. */
export async function getCategories(query: ShortlistQuery = {}): Promise<CategoryCount[]> {
  return live<CategoryCount[]>(`/shortlist/categories?${shortlistParams(query)}`);
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

// --- Ratings (per-advertiser like/neutral/dislike + comment) ---
// These hit the Go backend directly: ratings are fully backend-persisted.

export type Rating = "like" | "neutral" | "dislike" | "none";

export interface ApiRatingEntry {
  rating: Rating;
  comment: string;
}

/** Fetch all ratings for the given user. Returns a map keyed by advertiser_id. */
export async function getRatings(
  userEmail: string,
): Promise<Record<number, ApiRatingEntry>> {
  return live<Record<number, ApiRatingEntry>>(
    `/ratings?user_email=${encodeURIComponent(userEmail)}`,
  );
}

/** Set (or clear, when rating==="none") a rating for one advertiser. */
export async function setRating(
  advertiserId: number,
  userEmail: string,
  rating: Rating,
  comment: string,
): Promise<void> {
  await live(`/ratings/${advertiserId}`, {
    method: "POST",
    body: JSON.stringify({ user_email: userEmail, rating, comment }),
  });
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

/** Status of an on-demand enrichment job for a gem. */
export type EnrichStatus = {
  job_id: number;
  status: "none" | "running" | "done" | "error";
  error_text?: string | null;
};

/** Triggers an on-demand refresh of one gem; returns the running job's status. */
export async function postEnrich(advertiserId: number): Promise<EnrichStatus> {
  return live<EnrichStatus>(`/enrich/${advertiserId}`, { method: "POST", body: "{}" });
}

/** Polls the latest enrichment job status for a gem. */
export async function getEnrichStatus(advertiserId: number): Promise<EnrichStatus> {
  return live<EnrichStatus>(`/enrich/${advertiserId}`);
}

/** Fetch the preference-ranked queue for a user.
 * Returns rows sorted by the model's predicted like probability (p_like desc).
 * Returns [] when the user has no ratings yet. */
export async function getPreferenceQueue(userEmail: string): Promise<PreferenceCard[]> {
  const rows = await live<ApiPreferenceCardRow[]>(
    `/preference-queue?user_email=${encodeURIComponent(userEmail)}`,
  );
  return rows.map((r) => adaptPreferenceCard(r, mediaUrl));
}
