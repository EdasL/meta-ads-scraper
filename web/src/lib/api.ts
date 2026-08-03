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
  ApiSavedGroup,
  ApiSavedGroupPatch,
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
 * contract.
 *
 * This module is BROWSER-ONLY. All calls go through the session-checked
 * same-origin proxy (`/backend/*`, see `app/backend/[...path]/route.ts`), which
 * injects the internal API token + the session's `X-User-Email` server-side —
 * no API token ever reaches the client. Server code must NOT import this module
 * (the Go `/api` group now requires `X-User-Email`, so a bare internal-token
 * request would 401); call the API directly with `X-User-Email` instead.
 */
const API_BASE = "/backend";

/** Builds the media URL for an S3 object key. Always same-origin (`/backend/*`)
 *  so browser <img>/<video> tags carry the session cookie through the proxy —
 *  the api host now requires auth that a bare tag cannot send. */
const mediaUrl = (key: string) => `/backend/media/${key}`;

async function live<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    // no-store: the feed must reflect the live DB. Without it this Next version
    // caches the GET and the UI shows a stale count after a re-score (the
    // "stuck at ~300" symptom).
    cache: "no-store",
    ...init,
    headers: {
      "Content-Type": "application/json",
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

// --- Saved groups (the /saved folders) ---
// Unlike the saved-gem calls above, these take NO user_email: the owner is the
// session identity the proxy attaches (X-User-Email), so a caller cannot read
// or write another user's folders.

/** List the signed-in user's folders. Empty for a user who has never created
 *  one — the backend does not seed defaults. */
export async function getSavedGroups(): Promise<ApiSavedGroup[]> {
  return live<ApiSavedGroup[]>(`/saved-groups`);
}

/** Append a folder at the end; returns the created row (real id). */
export async function createSavedGroup(name: string): Promise<ApiSavedGroup> {
  return live<ApiSavedGroup>(`/saved-groups`, {
    method: "POST",
    body: JSON.stringify({ name }),
  });
}

/** Rename and/or reposition one folder. */
export async function updateSavedGroup(id: number, patch: ApiSavedGroupPatch): Promise<void> {
  await live(`/saved-groups/${id}/update`, { method: "POST", body: JSON.stringify(patch) });
}

/** Delete a folder; its membership rows die with it (CASCADE), the gems stay.
 *  Deleting down to zero folders is allowed. */
export async function removeSavedGroup(id: number): Promise<void> {
  await live(`/saved-groups/${id}/remove`, { method: "POST", body: "{}" });
}

/** Add a gem to a folder (idempotent — re-adding is a no-op server-side). */
export async function addSavedGroupGem(groupId: number, advertiserId: number): Promise<void> {
  await live(`/saved-groups/${groupId}/gems`, {
    method: "POST",
    body: JSON.stringify({ advertiser_id: advertiserId }),
  });
}

/** Remove a gem from a folder (idempotent). */
export async function removeSavedGroupGem(groupId: number, advertiserId: number): Promise<void> {
  await live(`/saved-groups/${groupId}/gems/remove`, {
    method: "POST",
    body: JSON.stringify({ advertiser_id: advertiserId }),
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
 * Scored at serve time from the user's latest ratings; `exclude` lists
 * advertiser_ids the deck already holds so refills never duplicate cards.
 * Returns [] when every candidate is rated. */
export async function getPreferenceQueue(
  userEmail: string,
  limit = 50,
  exclude: number[] = [],
): Promise<PreferenceCard[]> {
  const ex = exclude.length ? `&exclude=${exclude.join(",")}` : "";
  const rows = await live<ApiPreferenceCardRow[]>(
    `/preference-queue?user_email=${encodeURIComponent(userEmail)}&limit=${limit}${ex}`,
  );
  return rows.map((r) => adaptPreferenceCard(r, mediaUrl));
}

/** Marks the signed-in user as onboarded (first-run flow finished or skipped). */
export async function completeOnboarding(): Promise<void> {
  await live(`/me/onboarded`, { method: "POST", body: "{}" });
}

// --- Hunt (Peon-guided term search) ---
// Field names are the exact backend JSON (backend/models/hunt.go +
// hunt_controller.go); the Go /api group serves these under /hunts. All calls
// ride the same session-checked `/backend/*` proxy (hunts is allowlisted there).

/** One hunt thread in the list. `last_round_status` is null until a round runs.
 *  `last_activity` is the newest of the thread's create time, its latest message,
 *  and its latest round — the list is sorted newest-activity first on it. */
export interface HuntSummary {
  id: number;
  title: string;
  created_at: string;
  last_activity: string;
  last_round_status: string | null;
}

/** Optional per-round scrape-tuning knobs (backend clamps each on write:
 *  page_cap 1-100, patience 1-10, listing_pages 1-30, min_pages 1-30). Absent
 *  keys fall back to the server defaults (40 / 6 / 10 / 10). The wire shape is
 *  this object or `{}`. */
export interface HuntSettings {
  page_cap?: number;
  patience?: number;
  listing_pages?: number;
  /** Pages to read per term before the dry early-exit may fire (1-30). */
  min_pages?: number;
}

/** The assistant's structured suggestion. Stored jsonb carries the two term
 *  arrays plus an optional `settings` object — the conversational reply is the
 *  message content, not part of this. `settings` is absent on older messages. */
export interface HuntProposal {
  terms: string[];
  competitor_apps: string[];
  settings?: HuntSettings;
  /** The niche Peon is aiming at, in the founder's words — carried onto the
   *  round so triage can judge each found app as on- or off-target. Absent on
   *  older messages. */
  intent?: string;
}

/** One transcript entry. `proposal` is null for user messages. `tool_log` is the
 *  Peon tool trace (null / absent on messages that ran no tools). */
export interface HuntMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  proposal: HuntProposal | null;
  tool_log: string[] | null;
  created_at: string;
}

/** Live counters + append-only event log a running round writes to `progress`.
 *  Every field is optional: a freshly-queued round has `{}`. */
export interface HuntRoundProgress {
  /** Active lanes keyed by lane id, each with its current term + page — exactly
   *  as the Python producer writes it (src/agf/discovery/hunt.py _Progress).
   *  The active lane COUNT is `Object.keys(lanes).length`. */
  lanes?: Record<string, { term: string; page: number }>;
  terms_done?: number;
  terms_total?: number;
  found?: number;
  new?: number;
  events?: { t: string; msg: string }[];
}

/** One approved run of founder-blessed terms. */
export interface HuntRound {
  id: number;
  message_id: number | null;
  terms: string[];
  competitor_apps: string[];
  status: "queued" | "running" | "done" | "error";
  progress: HuntRoundProgress;
  /** The scrape settings this round ran with; `{}` when all defaults. */
  settings: HuntSettings;
  error_text: string | null;
  created_at: string;
  finished_at: string | null;
}

/** One (round, found page) row. `gem_score` is null until the page is kept + scored. */
export interface HuntResult {
  id: number;
  round_id: number;
  page_id: string;
  page_name: string | null;
  store: string;
  store_id: string;
  link_url: string | null;
  source_term: string;
  was_new: boolean;
  triage: "pending" | "kept" | "vetoed";
  kept_advertiser_id: number | null;
  gem_score: number | null;
  /** SERP page this app was first found on this round; null on pre-158 rows. */
  page: number | null;
  app_name: string | null;
  icon_url: string | null;
  tagline: string | null;
  cohort: string | null;
  /** True when the signed-in user already saved this advertiser. */
  saved: boolean;
  /** The signed-in user's rating for this advertiser, null when unrated. */
  rating: "like" | "neutral" | "dislike" | null;
  /** Peon's on-target judgement against the round's intent: "relevant" or
   *  "off_target", or null when the round carried no intent (nothing to judge). */
  relevance: "relevant" | "off_target" | null;
  /** One-line reason for an off-target call; null when relevant / unjudged. */
  relevance_note: string | null;
}

/** Full thread payload from GET /hunts/:id. */
export interface HuntThread {
  hunt: HuntSummary;
  messages: HuntMessage[];
  rounds: HuntRound[];
  results: HuntResult[];
}

/** Response of the chat POSTs (create hunt / append message). The backend's
 *  `writeThread` returns only the refreshed transcript, not rounds/results —
 *  the caller re-fetches the full thread via `getHunt` for those. */
export interface HuntChatTurn {
  hunt_id: number;
  messages: HuntMessage[];
}

/** All hunt threads for the signed-in user, newest first. */
export async function getHunts(): Promise<HuntSummary[]> {
  return live<HuntSummary[]>(`/hunts`);
}

/** Full thread (hunt + transcript + rounds + results). */
export async function getHunt(id: number): Promise<HuntThread> {
  return live<HuntThread>(`/hunts/${id}`);
}

/** Open a new thread with the founder's first message; returns the transcript. */
export async function postHunt(message: string): Promise<HuntChatTurn> {
  return live<HuntChatTurn>(`/hunts`, { method: "POST", body: JSON.stringify({ message }) });
}

/** The founder's live edits to the open proposal card, sent alongside a chat
 *  message so Peon reasons about the terms/competitors actually on screen. */
export type HuntCardState = { terms: string[]; competitor_apps: string[] };

/** Append a message to an existing thread; returns the refreshed transcript.
 *  When the founder has edited the open proposal card, pass its live state so
 *  the API hands Peon the on-screen terms rather than its own last suggestion. */
export async function postHuntMessage(
  id: number,
  message: string,
  cardState?: HuntCardState,
): Promise<HuntChatTurn> {
  return live<HuntChatTurn>(`/hunts/${id}/messages`, {
    method: "POST",
    body: JSON.stringify(cardState ? { message, card_state: cardState } : { message }),
  });
}

/** Delete a hunt thread and everything under it (messages, rounds, results).
 *  The backend cascades and returns 204; a raw fetch avoids parsing an empty
 *  body as JSON (which `live` would do). */
export async function deleteHunt(id: number): Promise<void> {
  const res = await fetch(`${API_BASE}/hunts/${id}`, {
    method: "DELETE",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
  });
  if (!res.ok) throw new Error(`API ${res.status}: ${res.statusText}`);
}

/** Enqueue an approved round (202). Returns the newly-queued round row. */
export async function postHuntRound(
  id: number,
  body: {
    message_id: number | null;
    terms: string[];
    competitor_apps: string[];
    settings?: HuntSettings;
    intent?: string;
  },
): Promise<HuntRound> {
  return live<HuntRound>(`/hunts/${id}/rounds`, { method: "POST", body: JSON.stringify(body) });
}
