/**
 * Wire shapes returned by the Plan 7 read-only FastAPI (src/agf/api/schemas.py).
 * These are the RAW server shapes — snake_case, nullable, and (for the dossier)
 * NESTED — which differ from the flat UI contract in `contracts.ts`. The mappers
 * in `lib/adapters.ts` translate Api* → the UI contract so components never see
 * the server shape. Keep these aligned field-for-field with the Pydantic models.
 */

/** `ShortlistRow` (schemas.py) — one matview row. Numeric axes are nullable. */
export interface ApiShortlistRow {
  advertiser_id: number;
  cohort: string;
  page_name: string | null;
  app_id: number | null;
  app_name: string | null;
  gem_score: number;
  scaling_velocity: number | null;
  under_radar: number | null;
  funnel_intent: number | null;
  active_ad_count: number | null;
  max_longevity_days: number | null;
  alert: boolean;
  refreshed_at: string | null;
}

/** `AppInfo` (schemas.py) — store listing joined off the gem's ads. */
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
}

/** `FunnelInfo` (schemas.py) — captured landing → redirect, screenshot key. */
export interface ApiFunnelInfo {
  landing_url: string | null;
  final_url_after_redirect: string | null;
  screenshot_s3_key: string | null;
}

/** `CreativeInfo` (schemas.py) — ad creative; `s3_key` is the S3 object key. */
export interface ApiCreativeInfo {
  type: string | null;
  s3_key: string | null;
  body_text: string | null;
  title: string | null;
}

/** `GemDossier` (schemas.py) — NESTED: the gem plus its joined enrichment. */
export interface ApiGemDossier {
  gem: ApiShortlistRow;
  domain: string | null;
  page_category: string | null;
  ig_username: string | null;
  ig_followers: number | null;
  parent_company: string | null; // resolved name of the owning advertiser, if any
  apps: ApiAppInfo[];
  funnels: ApiFunnelInfo[];
  creatives: ApiCreativeInfo[];
}

/** `ScalingPoint` (schemas.py) — one weekly snapshot. Note `total_eu_reach`. */
export interface ApiScalingPoint {
  week_start: string;
  active_ad_count: number | null;
  distinct_creative_count: number | null;
  total_eu_reach: number | null;
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
