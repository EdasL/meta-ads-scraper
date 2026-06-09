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
  ApiReviewQueueRow,
  ApiCreativeInfo,
  ApiFunnelInfo,
} from "@/types/api";
import type {
  ShortlistRow,
  GemDossier,
  ScalingPoint,
  ReviewQueueRow,
  Creative,
  FunnelStep,
  CompanyInfo,
  CreativeType,
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

export function adaptShortlistRow(r: ApiShortlistRow): ShortlistRow {
  return {
    advertiser_id: r.advertiser_id,
    cohort: r.cohort,
    page_name: r.page_name ?? "",
    app_id: r.app_id ?? null,
    app_name: r.app_name ?? null,
    gem_score: num(r.gem_score),
    scaling_velocity: num(r.scaling_velocity),
    under_radar: num(r.under_radar),
    funnel_intent: num(r.funnel_intent),
    active_ad_count: num(r.active_ad_count),
    max_longevity_days: num(r.max_longevity_days),
    alert: Boolean(r.alert),
    refreshed_at: r.refreshed_at ?? "",
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

/** Resolves an S3 object key to a browser-fetchable URL (the /api/media proxy). */
export type MediaUrl = (key: string) => string;

function adaptCreative(c: ApiCreativeInfo, i: number, mediaUrl: MediaUrl): Creative {
  const type: CreativeType = c.type === "video" ? "video" : "image";
  return {
    id: c.s3_key ?? `creative-${i}`,
    type,
    title: c.title ?? undefined,
    body_text: c.body_text ?? undefined,
    // the API returns an S3 key; the media proxy serves the bytes. The Dossier
    // falls back to a glyph if the image 404s (e.g. proxy not deployed yet).
    snapshot_image_url: c.s3_key ? mediaUrl(c.s3_key) : undefined,
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
 * Fold the nested API dossier + a separate /scaling response into one flat
 * `GemDossier`. `scaling` is a separate endpoint server-side but the dossier
 * page reads `gem.scaling` inline, so the live client fetches both and merges.
 */
export function adaptDossier(
  d: ApiGemDossier,
  scaling: ApiScalingPoint[],
  mediaUrl: MediaUrl,
): GemDossier {
  const base = adaptShortlistRow(d.gem);
  const company: CompanyInfo = {
    domain: d.domain ?? undefined,
    page_category: d.page_category ?? undefined,
    parent_company: d.parent_company ?? null,
    ig_username: d.ig_username ?? undefined,
    ig_followers: d.ig_followers ?? undefined,
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
    company,
  };
}
