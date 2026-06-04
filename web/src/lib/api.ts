import type {
  GemDossier,
  ReviewDecision,
  ReviewQueueRow,
  ScalingPoint,
  ShortlistQuery,
  ShortlistRow,
} from "@/types/contracts";
import { GEMS } from "./mock/fixtures";

/**
 * Data mode: "mock" (fixtures, default) or "live" (Plan 7 FastAPI).
 * Set NEXT_PUBLIC_DATA_MODE=live and NEXT_PUBLIC_API_URL to flip to the
 * real read-only API once the pipeline (Plans 2-7) is running.
 */
const MODE = process.env.NEXT_PUBLIC_DATA_MODE ?? "mock";
const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api";

const REVIEW_DECISION_KEY = "fielddesk.decisions";

function delay<T>(value: T, ms = 280): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), ms));
}

function toRow(g: GemDossier): ShortlistRow {
  const { creatives, funnel, scaling, company, tagline, pricing, ...row } = g;
  void creatives;
  void funnel;
  void scaling;
  void company;
  void tagline;
  void pricing;
  return row;
}

/** Decisions persisted client-side in mock mode so the queue updates. */
function decidedIds(): Set<number> {
  if (typeof window === "undefined") return new Set();
  try {
    const raw = window.localStorage.getItem(REVIEW_DECISION_KEY);
    const obj = raw ? (JSON.parse(raw) as Record<string, ReviewDecision>) : {};
    return new Set(Object.keys(obj).map(Number));
  } catch {
    return new Set();
  }
}

async function live<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) throw new Error(`API ${res.status}: ${res.statusText}`);
  return (await res.json()) as T;
}

export async function getShortlist(query: ShortlistQuery = {}): Promise<ShortlistRow[]> {
  if (MODE === "live") {
    const params = new URLSearchParams();
    if (query.cohort) params.set("cohort", query.cohort);
    if (query.min_score != null) params.set("min_score", String(query.min_score));
    return live<ShortlistRow[]>(`/shortlist?${params}`);
  }
  let rows = GEMS.map(toRow);
  if (query.cohort) rows = rows.filter((r) => r.cohort === query.cohort);
  if (query.min_score != null) rows = rows.filter((r) => r.gem_score >= query.min_score!);
  rows.sort((a, b) => b.gem_score - a.gem_score);
  return delay(rows);
}

export async function getGem(advertiserId: number): Promise<GemDossier | null> {
  if (MODE === "live") return live<GemDossier>(`/gems/${advertiserId}`);
  const gem = GEMS.find((g) => g.advertiser_id === advertiserId) ?? null;
  return delay(gem);
}

export async function getScaling(advertiserId: number): Promise<ScalingPoint[]> {
  if (MODE === "live") return live<ScalingPoint[]>(`/gems/${advertiserId}/scaling`);
  const gem = GEMS.find((g) => g.advertiser_id === advertiserId);
  return delay(gem?.scaling ?? []);
}

export async function getReviewQueue(): Promise<ReviewQueueRow[]> {
  if (MODE === "live") return live<ReviewQueueRow[]>(`/review/queue`);
  const decided = decidedIds();
  const rows = GEMS.filter((g) => g.gem_score >= 70 && !decided.has(g.advertiser_id))
    .sort((a, b) => b.gem_score - a.gem_score)
    .map((g) => ({
      advertiser_id: g.advertiser_id,
      cohort: g.cohort,
      page_name: g.page_name,
      app_name: g.app_name,
      gem_score: g.gem_score,
      active_ad_count: g.active_ad_count,
      max_longevity_days: g.max_longevity_days,
    }));
  return delay(rows);
}

export async function postReviewDecision(
  advertiserId: number,
  decision: ReviewDecision,
): Promise<{ ok: true }> {
  if (MODE === "live") {
    return live<{ ok: true }>(`/review/${advertiserId}/decision`, {
      method: "POST",
      body: JSON.stringify(decision),
    });
  }
  if (typeof window !== "undefined") {
    const raw = window.localStorage.getItem(REVIEW_DECISION_KEY);
    const obj = raw ? (JSON.parse(raw) as Record<string, ReviewDecision>) : {};
    obj[advertiserId] = decision;
    window.localStorage.setItem(REVIEW_DECISION_KEY, JSON.stringify(obj));
  }
  return delay({ ok: true } as const);
}

export const DATA_MODE = MODE;
