/**
 * Translate the Plan 7 read-only FastAPI wire shapes (`types/api.ts`) into the
 * flat UI contract (`types/contracts.ts`). The server's dossier is NESTED and
 * snake_case with nullable axes and S3 keys; the UI wants one flat object with
 * numeric axes and ready-to-render fields. These are PURE functions — unit-tested
 * in `adapters.test.ts` (run with `node --test`), no React/fetch here.
 *
 * Graceful degradation: fields the API does not yet expose (tagline, pricing,
 * Instagram, tech stack, domain age, creative/screenshot image URLs) are left
 * undefined. The dossier's `MarginNote`/`Polaroid` hide undefined values, so the
 * page renders cleanly until the backend grows those fields (iteration 2:
 * persist tech/age/price + a `/api/media/{key}` route — see docs/plans).
 */
import type {
  ApiShortlistRow,
  ApiGemDossier,
  ApiScalingPoint,
  ApiAppMetricPoint,
  ApiReviewQueueRow,
  ApiCreativeInfo,
  ApiFunnelInfo,
  ApiAppInfo,
  ApiAppReview,
  ApiExplainer,
  ApiGrowth,
  ApiHeroCreative,
} from "@/types/api";
import type {
  ShortlistRow,
  GemDossier,
  ScalingPoint,
  AppMetricPoint,
  ReviewQueueRow,
  Creative,
  FunnelStep,
  CompanyInfo,
  AppInfo,
  AppReview,
  CreativeType,
  Explainer,
  Growth,
  HeroCreative,
} from "@/types/contracts";

/** Null/undefined numeric → default (UI types axes as non-null; bars do value×100). */
const num = (v: number | null | undefined, fallback = 0): number =>
  v == null || Number.isNaN(v) ? fallback : v;

/** Bare hostname (no leading www.) for a URL, or a readable fallback. */
function hostLabel(url: string | null | undefined): string {
  if (!url) return "funnel step";
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

/** Resolves an S3 object key to a browser-fetchable URL (the /api/media proxy). */
export type MediaUrl = (key: string) => string;

/** Explainer → null when the LLM hasn't produced any of the three fields. */
export function adaptExplainer(e: ApiExplainer | null | undefined): Explainer | null {
  if (!e) return null;
  const what_it_does = e.what_it_does ?? "";
  const problem_solved = e.problem_solved ?? "";
  const why_working = e.why_working ?? "";
  if (!what_it_does && !problem_solved && !why_working) return null;
  return { what_it_does, problem_solved, why_working };
}

/** Growth → null when the backend has no growth row; pct_change stays nullable. */
export function adaptGrowth(g: ApiGrowth | null | undefined): Growth | null {
  if (!g) return null;
  return {
    first_count: num(g.first_count),
    latest_count: num(g.latest_count),
    weeks: num(g.weeks),
    pct_change: g.pct_change == null || Number.isNaN(g.pct_change) ? null : g.pct_change,
  };
}

/** Hero creative → null when no usable creative; image URL via the media proxy. */
export function adaptHeroCreative(
  h: ApiHeroCreative | null | undefined,
  mediaUrl: MediaUrl,
): HeroCreative | null {
  if (!h) return null;
  const type: CreativeType = h.type === "video" ? "video" : "image";
  return {
    type,
    title: h.title ?? undefined,
    body_text: h.body_text ?? undefined,
    snapshot_image_url: h.s3_key ? mediaUrl(h.s3_key) : undefined,
  };
}

export function adaptShortlistRow(r: ApiShortlistRow, mediaUrl: MediaUrl): ShortlistRow {
  return {
    advertiser_id: r.advertiser_id,
    cohort: r.cohort,
    page_name: r.page_name ?? "",
    app_id: r.app_id ?? null,
    app_name: r.app_name ?? null,
    icon_url: r.icon_url ?? undefined,
    gem_score: num(r.gem_score),
    scaling_velocity: num(r.scaling_velocity),
    under_radar: num(r.under_radar),
    funnel_intent: num(r.funnel_intent),
    active_ad_count: num(r.active_ad_count),
    max_longevity_days: num(r.max_longevity_days),
    alert: Boolean(r.alert),
    refreshed_at: r.refreshed_at ?? "",
    explainer: adaptExplainer(r.explainer),
    growth: adaptGrowth(r.growth),
    hero_creative: adaptHeroCreative(r.hero_creative, mediaUrl),
  };
}

export function adaptScalingPoint(p: ApiScalingPoint): ScalingPoint {
  return {
    week_start: p.week_start,
    active_ad_count: num(p.active_ad_count),
    distinct_creative_count: num(p.distinct_creative_count),
    eu_total_reach: p.total_eu_reach ?? undefined, // API: total_eu_reach → UI: eu_total_reach
  };
}

/** Daily app-store snapshot → UI point; nulls become undefined (optional). */
export function adaptAppMetricPoint(p: ApiAppMetricPoint): AppMetricPoint {
  return {
    app_id: p.app_id,
    store: p.store,
    snapshot_date: p.snapshot_date,
    rating: p.rating ?? undefined,
    rating_count: p.rating_count ?? undefined,
    installs: p.installs ?? undefined,
  };
}

export function adaptReviewRow(r: ApiReviewQueueRow): ReviewQueueRow {
  return {
    advertiser_id: r.advertiser_id,
    cohort: r.cohort,
    page_name: r.page_name ?? "",
    app_name: r.app_name ?? null,
    gem_score: num(r.gem_score),
    active_ad_count: num(r.active_ad_count),
    max_longevity_days: num(r.max_longevity_days),
  };
}

function adaptCreative(c: ApiCreativeInfo, i: number, mediaUrl: MediaUrl): Creative {
  const type: CreativeType =
    c.type === "video" ? "video" : c.type === "text" ? "text" : "image";
  return {
    id: c.external_ad_id ?? c.s3_key ?? `creative-${i}`,
    type,
    title: c.title ?? undefined,
    body_text: c.body_text ?? undefined,
    // the API returns an S3 key; the media proxy serves the bytes. The Dossier
    // falls back to a glyph if the image 404s (e.g. proxy not deployed yet).
    snapshot_image_url: c.s3_key ? mediaUrl(c.s3_key) : undefined,
    external_ad_id: c.external_ad_id ?? undefined,
    cta_text: c.cta_text ?? undefined,
    link_url: c.link_url ?? undefined,
    ad_started_on: c.ad_started_on ?? undefined,
    ad_ended_on: c.ad_ended_on ?? undefined,
    ad_active: c.ad_active ?? undefined,
    ad_days_running: c.ad_days_running ?? undefined,
    publisher_platforms:
      c.publisher_platforms && c.publisher_platforms.length > 0
        ? c.publisher_platforms
        : undefined,
    eu_total_reach: c.eu_total_reach ?? undefined,
  };
}

/** Store review → UI AppReview; empty strings/nulls become undefined. */
function adaptAppReview(r: ApiAppReview): AppReview {
  return {
    author: r.author || undefined,
    rating: r.rating ?? undefined,
    title: r.title || undefined,
    content: r.content || undefined,
    reviewed_at: r.reviewed_at ?? undefined,
  };
}

/** Store listing → UI AppInfo. Drops apps with an unknown store or no id. */
function adaptApp(a: ApiAppInfo): AppInfo | null {
  if (!a.store_id) return null;
  const store: AppInfo["store"] | null =
    a.store === "ios" ? "ios" : a.store === "android" ? "android" : null;
  if (!store) return null;
  return {
    store,
    store_id: a.store_id,
    name: a.name ?? undefined,
    developer: a.developer ?? undefined,
    genre: a.genre ?? undefined,
    release_date: a.release_date ?? undefined,
    rating: a.rating ?? undefined,
    rating_count: a.rating_count ?? undefined,
    is_emerging: a.is_emerging ?? undefined,
    description: a.description || undefined,
    icon_url: a.icon_url || undefined,
    screenshots: a.screenshots && a.screenshots.length > 0 ? a.screenshots : undefined,
    price_label: a.price_label || undefined,
    installs: a.installs ?? undefined,
    updated_at: a.updated_at ?? undefined,
    reviews: a.reviews ? a.reviews.map(adaptAppReview) : undefined,
  };
}

function adaptFunnelStep(f: ApiFunnelInfo, i: number, mediaUrl: MediaUrl): FunnelStep {
  return {
    step: i + 1,
    label: hostLabel(f.final_url_after_redirect ?? f.landing_url),
    landing_url: f.landing_url ?? "",
    final_url: f.final_url_after_redirect ?? undefined,
    screenshot_url: f.screenshot_s3_key ? mediaUrl(f.screenshot_s3_key) : undefined,
  };
}

/**
 * Fold the nested API dossier + the separate /scaling and /app-metrics
 * responses into one flat `GemDossier`. Both are separate endpoints
 * server-side but the dossier page reads `gem.scaling` / `gem.app_metrics`
 * inline, so the live client fetches all three and merges.
 */
export function adaptDossier(
  d: ApiGemDossier,
  scaling: ApiScalingPoint[],
  appMetrics: ApiAppMetricPoint[],
  mediaUrl: MediaUrl,
): GemDossier {
  const base = adaptShortlistRow(d.gem, mediaUrl);
  const company: CompanyInfo = {
    domain: d.domain ?? undefined,
    page_category: d.page_category ?? undefined,
    parent_company: d.parent_company ?? null,
    ig_username: d.ig_username ?? undefined,
    ig_followers: d.ig_followers ?? undefined,
    page_id: d.page_id ?? undefined, // powers Profile / Ads Library links
    // not yet exposed by the API — hidden by the UI until iteration 2
    // (persist tech-stack + WHOIS domain age, then map here):
    domain_age_years: undefined,
    tech: undefined,
  };
  return {
    ...base,
    tagline: "", // no server-side source; mock-only flavor text
    pricing: undefined,
    creatives: d.creatives.map((c, i) => adaptCreative(c, i, mediaUrl)),
    funnel: d.funnels.map((f, i) => adaptFunnelStep(f, i, mediaUrl)),
    scaling: scaling.map(adaptScalingPoint),
    app_metrics: appMetrics.map(adaptAppMetricPoint),
    company,
    apps: d.apps.map(adaptApp).filter((a): a is AppInfo => a !== null),
    ads_last_seen: d.ads_last_seen ?? undefined,
    page_like_count: d.page_like_count ?? undefined,
    meta_active_ads: d.meta_active_ads ?? undefined,
    meta_active_ads_capped: d.meta_active_ads_capped ?? undefined,
    meta_active_ads_checked_at: d.meta_active_ads_checked_at ?? undefined,
  };
}
