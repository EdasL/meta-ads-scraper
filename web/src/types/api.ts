/**
 * Wire shapes returned by the Plan 7 read-only FastAPI (src/agf/api/schemas.py).
 * These are the RAW server shapes — snake_case, nullable, and (for the dossier)
 * NESTED — which differ from the flat UI contract in `contracts.ts`. The mappers
 * in `lib/adapters.ts` translate Api* → the UI contract so components never see
 * the server shape. Keep these aligned field-for-field with the Pydantic models.
 */

/** `ExplainerInfo` (backend models.go) — LLM explainer; null until generated. */
export interface ApiExplainer {
  what_it_does: string | null;
  problem_solved: string | null;
  why_working: string | null;
}

/** `GrowthInfo` (backend models.go) — ad-count growth; nullable pct_change. */
export interface ApiGrowth {
  first_count: number | null;
  latest_count: number | null;
  weeks: number | null;
  pct_change: number | null;
}

/** `CreativeInfo` reused for the card hero — one real ad; `s3_key` is the key. */
export interface ApiHeroCreative {
  type: string | null;
  s3_key: string | null;
  body_text: string | null;
  title: string | null;
}

/** `ShortlistRow` (schemas.py) — one matview row. Numeric axes are nullable. */
export interface ApiShortlistRow {
  advertiser_id: number;
  cohort: string;
  page_name: string | null;
  app_id: number | null;
  app_name: string | null;
  /** Store icon from the apps join (migration 113); optional for older builds. */
  icon_url?: string | null;
  gem_score: number;
  scaling_velocity: number | null;
  under_radar: number | null;
  funnel_intent: number | null;
  active_ad_count: number | null;
  max_longevity_days: number | null;
  alert: boolean;
  refreshed_at: string | null;
  // New (Track B): embedded on the row so the card needs no second fetch.
  // Absent/null when the explainer/growth/hero hasn't been computed yet.
  explainer?: ApiExplainer | null;
  growth?: ApiGrowth | null;
  hero_creative?: ApiHeroCreative | null;
  // Ad-lifecycle card fields (matview migration 114); absent on older builds.
  first_ad_at?: string | null;
  longrun_ad_count?: number | null;
  new_ads_4w?: number | null;
  ads_seen?: number | null;
}

/** `AppReview` (backend models.go) — one recent store review for the app. */
export interface ApiAppReview {
  author: string | null;
  rating: number | null;
  title: string | null;
  content: string | null;
  reviewed_at: string | null;
}

/** `AppInfo` (schemas.py / models.go) — store listing joined off the gem's ads.
 * The store-preview fields (description…reviews) are optional so the client
 * tolerates older API builds that don't return them yet. */
export interface ApiAppInfo {
  store: string;
  store_id: string;
  name: string | null;
  developer: string | null;
  genre: string | null;
  release_date: string | null;
  rating: number | null;
  rating_count: number | null;
  is_emerging: boolean | null;
  description?: string | null;
  icon_url?: string | null;
  screenshots?: string[] | null;
  price_label?: string | null;
  installs?: number | null;
  updated_at?: string | null;
  reviews?: ApiAppReview[] | null;
}

/** `FunnelInfo` (schemas.py) — captured landing → redirect, screenshot key. */
export interface ApiFunnelInfo {
  landing_url: string | null;
  final_url_after_redirect: string | null;
  screenshot_s3_key: string | null;
}

/** `CreativeInfo` (schemas.py / models.go) — ad creative; `s3_key` is the S3
 * object key. Per-ad meta fields are optional (older API builds omit them). */
export interface ApiCreativeInfo {
  type: string | null;
  s3_key: string | null;
  body_text: string | null;
  title: string | null;
  external_ad_id?: string | null;
  cta_text?: string | null;
  link_url?: string | null;
  ad_started_on?: string | null;
  ad_ended_on?: string | null;
  ad_active?: boolean | null;
  ad_days_running?: number | null;
  publisher_platforms?: string[] | null;
  eu_total_reach?: number | null;
}

/** `GemDossier` (schemas.py) — NESTED: the gem plus its joined enrichment.
 * The ad-library fields (ads_last_seen…meta_active_ads_checked_at) are optional
 * so the client tolerates older API builds that don't return them yet. */
export interface ApiGemDossier {
  gem: ApiShortlistRow;
  page_id: string | null; // Facebook page id — powers Profile / Ads Library links
  domain: string | null;
  page_category: string | null;
  ig_username: string | null;
  ig_followers: number | null;
  parent_company: string | null; // resolved name of the owning advertiser, if any
  apps: ApiAppInfo[];
  funnels: ApiFunnelInfo[];
  creatives: ApiCreativeInfo[];
  ads_last_seen?: string | null; // advertisers.last_seen — freshness of the ad data
  page_like_count?: number | null;
  meta_active_ads?: number | null; // true ad-library count (bounded pagination)
  meta_active_ads_capped?: boolean | null; // true when the count hit the cap
  meta_active_ads_checked_at?: string | null;
  ad_lifecycle?: ApiAdLifecycle | null; // absent/null on older builds
}

/** `ScalingPoint` (schemas.py) — one weekly snapshot. Note `total_eu_reach`. */
export interface ApiScalingPoint {
  week_start: string;
  active_ad_count: number | null;
  /** @deprecated The read-time series serves 0; kept for contract compatibility. */
  distinct_creative_count: number | null;
  total_eu_reach: number | null;
  // Launched/ended decomposition (read-time reconstruction); absent on older builds.
  launched_count?: number | null;
  ended_count?: number | null;
}

/** `AdLifecycle` (backend models.go) — lifecycle summary over all stored ads. */
export interface ApiAdLifecycle {
  ads_seen: number | null;
  active_now: number | null;
  running_30d_plus: number | null;
  median_run_days: number | null; // null when fewer than 5 ads have ended
  first_ad_at: string | null;
  launched_4w: number | null;
  ended_4w: number | null;
}

/** `AppMetricPoint` (backend models.go) — one daily app-store snapshot from
 * `GET /api/gems/{advertiser_id}/app-metrics`, ordered by snapshot_date ASC. */
export interface ApiAppMetricPoint {
  app_id: number;
  store: string;
  snapshot_date: string;
  rating: number | null;
  rating_count: number | null;
  installs: number | null;
}

/**
 * `ReviewQueueRow` (schemas.py). `active_ad_count` / `max_longevity_days` are
 * optional here because older API builds omitted them; the live API now returns
 * both (matview columns) so the review slip can show "N ads now · Md up".
 */
export interface ApiReviewQueueRow {
  advertiser_id: number;
  cohort: string;
  page_name: string | null;
  app_name: string | null;
  gem_score: number;
  active_ad_count?: number | null;
  max_longevity_days?: number | null;
}

/** `SavedGemRow` (backend models.go) — a user's watchlist entry; score axes are
 * the snapshot frozen at save time, the rest are the user's annotations. */
export interface ApiSavedGem {
  user_email: string;
  advertiser_id: number;
  page_name: string | null;
  app_name: string | null;
  icon_url: string | null;
  cohort: string;
  gem_score: number;
  scaling_velocity: number | null;
  under_radar: number | null;
  funnel_intent: number | null;
  active_ad_count: number | null;
  tags: string[];
  note: string;
  stage: string;
  verdict: string | null;
  confidence: number | null;
  saved_at: string | null;
  updated_at: string | null;
}

/** `SavedGemIn` (backend) — the upsert body. On a re-save only the mutable
 * annotations (tags/note/stage/verdict/confidence) are applied server-side. */
export interface ApiSavedGemIn {
  user_email: string;
  page_name: string | null;
  app_name: string | null;
  icon_url: string | null;
  cohort: string;
  gem_score: number;
  scaling_velocity: number | null;
  under_radar: number | null;
  funnel_intent: number | null;
  active_ad_count: number | null;
  tags: string[];
  note: string;
  stage: string;
  verdict: string | null;
  confidence: number | null;
}
