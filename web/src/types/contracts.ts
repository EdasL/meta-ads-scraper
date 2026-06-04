/**
 * Frozen API contract — mirrors docs/plans/00-master-plan.md §3.6 (matview
 * `frontend.emerging_scaling_apps`) and §3.7 (review keyed on advertiser_id).
 * The UI consumes these shapes whether data comes from mocks or the Plan 7
 * read-only FastAPI. Keep field names exactly aligned with the matview.
 */

/** One row of `frontend.emerging_scaling_apps` (§3.6). */
export interface ShortlistRow {
  advertiser_id: number;
  cohort: string; // category x country, e.g. "Health & Fitness · US"
  page_name: string;
  app_id: number | null; // NULL for web funnels
  app_name: string | null;
  gem_score: number; // 0-100
  scaling_velocity: number; // 0-1 axis
  under_radar: number; // 0-1 axis
  funnel_intent: number; // 0-1 axis
  active_ad_count: number;
  max_longevity_days: number;
  alert: boolean;
  refreshed_at: string; // ISO timestamp
}

export type CreativeType = "image" | "video";

export interface Creative {
  id: string;
  type: CreativeType;
  title?: string;
  body_text?: string;
  snapshot_image_url?: string;
}

export interface FunnelStep {
  step: number;
  label: string;
  landing_url: string;
  final_url?: string;
  screenshot_url?: string;
}

/** A weekly scaling snapshot point. */
export interface ScalingPoint {
  week_start: string; // ISO date
  active_ad_count: number;
  distinct_creative_count: number;
  eu_total_reach?: number;
}

export interface CompanyInfo {
  domain?: string;
  domain_age_years?: number;
  parent_company?: string | null;
  page_category?: string;
  ig_username?: string;
  ig_followers?: number;
  tech?: string[];
}

/** Full case file for one gem (§3.6 row + enrichment from Plans 5/6). */
export interface GemDossier extends ShortlistRow {
  tagline: string;
  pricing?: string;
  creatives: Creative[];
  funnel: FunnelStep[];
  scaling: ScalingPoint[];
  company: CompanyInfo;
}

/** Review queue row (undecided, gem_score >= 70) — §3.7. */
export interface ReviewQueueRow {
  advertiser_id: number;
  cohort: string;
  page_name: string;
  app_name: string | null;
  gem_score: number;
  active_ad_count: number;
  max_longevity_days: number;
}

export type Verdict = "accept" | "reject" | "hold";

export interface ReviewDecision {
  verdict: Verdict;
  reviewer: string;
  note?: string;
}

export interface ShortlistQuery {
  cohort?: string;
  min_score?: number;
}
