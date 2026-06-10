/**
 * Frozen API contract — mirrors docs/plans/00-master-plan.md §3.6 (matview
 * `frontend.emerging_scaling_apps`) and §3.7 (review keyed on advertiser_id).
 * The UI consumes these shapes whether data comes from mocks or the Plan 7
 * read-only FastAPI. Keep field names exactly aligned with the matview.
 */

/**
 * Plain-language explainer for a gem — three short fields, LLM-generated on
 * the Go backend (grounded in the scraped ad copy + app-store description).
 * Null until the explainer runner has produced it.
 */
export interface Explainer {
  what_it_does: string;
  problem_solved: string;
  why_working: string;
}

/**
 * Quantified ad-count growth over the observed weekly series. `pct_change` is
 * null when it can't be computed honestly (single week, or growth from zero).
 */
export interface Growth {
  first_count: number;
  latest_count: number;
  weeks: number;
  pct_change: number | null;
}

/** The single ad creative surfaced as the card hero (real scraped ad). */
export interface HeroCreative {
  type: CreativeType;
  snapshot_image_url?: string;
  title?: string;
  body_text?: string;
}

/** One row of `frontend.emerging_scaling_apps` (§3.6). */
export interface ShortlistRow {
  advertiser_id: number;
  cohort: string; // category x country, e.g. "Health & Fitness · US"
  page_name: string;
  app_id: number | null; // NULL for web funnels
  app_name: string | null;
  icon_url?: string; // app store icon; absent → initials tile in the UI
  gem_score: number; // 0-100
  scaling_velocity: number; // 0-1 axis
  under_radar: number; // 0-1 axis
  funnel_intent: number; // 0-1 axis
  active_ad_count: number;
  max_longevity_days: number;
  alert: boolean;
  refreshed_at: string; // ISO timestamp
  explainer: Explainer | null; // LLM explainer (null until generated)
  growth: Growth | null; // quantified ad-count growth (null when sparse)
  hero_creative: HeroCreative | null; // real ad creative for the card hero
}

export type CreativeType = "image" | "video" | "text";

/** One ad card: the ad's best creative plus its run metadata. */
export interface Creative {
  id: string;
  type: CreativeType;
  title?: string;
  body_text?: string;
  snapshot_image_url?: string;
  /** Meta Ad Library ID — links the card to the ad's own library page. */
  external_ad_id?: string;
  cta_text?: string;
  link_url?: string;
  /** ISO date the ad started running (ads.start_date). */
  ad_started_on?: string;
  /** ISO date the ad stopped (ads.end_date) — meaningful only when not active. */
  ad_ended_on?: string;
  ad_active?: boolean;
  /** Days the ad has run (active: today−start; ended: end−start). */
  ad_days_running?: number;
  publisher_platforms?: string[];
  /** Latest EU reach for this creative's ad. US ads have none — never faked. */
  eu_total_reach?: number;
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
  page_id?: string; // Facebook page id — powers Profile / Ads Library links
}

/** One recent store review for the gem's app. */
export interface AppReview {
  author?: string;
  rating?: number;
  title?: string;
  content?: string;
  reviewed_at?: string; // ISO timestamp
}

/** A store listing for the gem's app (iOS or Android), joined off its ads. */
export interface AppInfo {
  store: "ios" | "android";
  store_id: string;
  name?: string;
  developer?: string;
  genre?: string;
  release_date?: string; // ISO date
  rating?: number;
  rating_count?: number;
  is_emerging?: boolean;
  description?: string;
  icon_url?: string;
  screenshots?: string[];
  price_label?: string;
  installs?: number; // near-exact live install count (Play only)
  updated_at?: string; // when the store data was last refreshed
  reviews?: AppReview[];
}

/** One daily app-store snapshot (adtech.app_metrics) for the growth chart. */
export interface AppMetricPoint {
  app_id: number;
  store: string;
  snapshot_date: string; // ISO date
  rating?: number;
  rating_count?: number;
  installs?: number;
}

/** Full case file for one gem (§3.6 row + enrichment from Plans 5/6). */
export interface GemDossier extends ShortlistRow {
  tagline: string;
  pricing?: string;
  creatives: Creative[];
  funnel: FunnelStep[];
  scaling: ScalingPoint[];
  company: CompanyInfo;
  apps: AppInfo[];
  /** Daily app-store snapshots (merged from /gems/{id}/app-metrics). */
  app_metrics: AppMetricPoint[];
  /** When the advertiser's ads were last seen by the scraper. */
  ads_last_seen?: string;
  page_like_count?: number;
  /** True ad-library count (bounded pagination); falls back to active_ad_count. */
  meta_active_ads?: number;
  meta_active_ads_capped?: boolean;
  meta_active_ads_checked_at?: string;
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
