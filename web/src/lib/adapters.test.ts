/**
 * Pure-mapper tests for lib/adapters.ts. Zero-dependency: run with
 *   node --test src/lib/adapters.test.ts
 * (Node 22 strips the TS types at load; this file is excluded from tsc/Next via
 * tsconfig `exclude`, so the `.ts`-extension import below is fine.)
 *
 * Fixtures are literal copies of the documented FastAPI response shapes
 * (src/agf/api/schemas.py / queries.py) so the mappers are pinned to the real
 * contract, not to my memory of it.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  adaptShortlistRow,
  adaptScalingPoint,
  adaptAppMetricPoint,
  adaptReviewRow,
  adaptDossier,
  adaptExplainer,
  adaptGrowth,
  adaptHeroCreative,
} from "./adapters.ts";

const media = (key) => `/api/media/${key}`;

test("adaptShortlistRow coerces null axes to 0 and null page_name to ''", () => {
  const row = adaptShortlistRow({
    advertiser_id: 42,
    cohort: "Health & Fitness×US",
    page_name: null,
    app_id: null,
    app_name: null,
    gem_score: 88,
    scaling_velocity: null,
    under_radar: null,
    funnel_intent: null,
    active_ad_count: null,
    max_longevity_days: null,
    alert: true,
    refreshed_at: null,
  }, media);
  assert.equal(row.page_name, "");
  assert.equal(row.scaling_velocity, 0);
  assert.equal(row.under_radar, 0);
  assert.equal(row.funnel_intent, 0);
  assert.equal(row.active_ad_count, 0);
  assert.equal(row.max_longevity_days, 0);
  assert.equal(row.alert, true);
  assert.equal(row.refreshed_at, "");
  assert.equal(row.gem_score, 88);
  // absent new fields → null (graceful degradation)
  assert.equal(row.explainer, null);
  assert.equal(row.growth, null);
  assert.equal(row.hero_creative, null);
  // GemScore v2 fields absent on old builds → undefined, never 0 (a 0 delta is data)
  assert.equal(row.installs_delta_28d, undefined);
  assert.equal(row.rating_count_delta_28d, undefined);
  assert.equal(row.niche_keyword, undefined);
  assert.equal(row.demand, undefined);
});

test("adaptShortlistRow passes GemScore v2 fields through, keeping real zeros", () => {
  const row = adaptShortlistRow({
    advertiser_id: 2, cohort: "Education×US", page_name: "Beta",
    app_id: 8, app_name: "BetaApp", gem_score: 61.2,
    scaling_velocity: 0.6, under_radar: 0.7, funnel_intent: 0.5,
    active_ad_count: 4, max_longevity_days: 21, alert: false,
    refreshed_at: "2026-06-11T08:00:00Z",
    demand: 0.8, whitespace: 0.9,
    niche_keyword: "ai calorie tracker", niche_advertiser_count: 2,
    installs_delta_28d: 4000, rating_count_delta_28d: 0,
  }, media);
  assert.equal(row.demand, 0.8);
  assert.equal(row.whitespace, 0.9);
  assert.equal(row.niche_keyword, "ai calorie tracker");
  assert.equal(row.niche_advertiser_count, 2);
  assert.equal(row.installs_delta_28d, 4000);
  assert.equal(row.rating_count_delta_28d, 0);   // a real zero survives
});

test("adaptShortlistRow passes real values through unchanged", () => {
  const row = adaptShortlistRow({
    advertiser_id: 1, cohort: "Education×US", page_name: "Alpha",
    app_id: 7, app_name: "AlphaApp", gem_score: 74.5,
    scaling_velocity: 0.9, under_radar: 0.8, funnel_intent: 0.7,
    active_ad_count: 6, max_longevity_days: 30, alert: false,
    refreshed_at: "2026-06-04T08:00:00Z",
  }, media);
  assert.equal(row.app_id, 7);
  assert.equal(row.scaling_velocity, 0.9);
  assert.equal(row.alert, false);
  assert.equal(row.refreshed_at, "2026-06-04T08:00:00Z");
});

test("adaptShortlistRow maps embedded explainer/growth/hero_creative", () => {
  const row = adaptShortlistRow({
    advertiser_id: 5, cohort: "Health×US", page_name: "SleepWave",
    app_id: 90211, app_name: "SleepWave", gem_score: 88,
    scaling_velocity: 0.9, under_radar: 0.86, funnel_intent: 0.78,
    active_ad_count: 11, max_longevity_days: 28, alert: true,
    refreshed_at: "2026-06-04T08:00:00Z",
    explainer: { what_it_does: "sleep sounds app", problem_solved: "cant sleep", why_working: "good reviews" },
    growth: { first_count: 3, latest_count: 11, weeks: 4, pct_change: 267 },
    hero_creative: { type: "image", s3_key: "creatives/hero.jpg", body_text: "cant sleep?", title: "Watch this" },
  }, media);
  assert.equal(row.explainer.what_it_does, "sleep sounds app");
  assert.equal(row.growth.pct_change, 267);
  assert.equal(row.growth.latest_count, 11);
  assert.equal(row.hero_creative.type, "image");
  assert.equal(row.hero_creative.title, "Watch this");
  assert.equal(row.hero_creative.snapshot_image_url, "/api/media/creatives/hero.jpg");
});

test("adaptExplainer: null when missing or all-empty, else coerces nulls to ''", () => {
  assert.equal(adaptExplainer(null), null);
  assert.equal(adaptExplainer({ what_it_does: null, problem_solved: null, why_working: null }), null);
  const e = adaptExplainer({ what_it_does: "does X", problem_solved: null, why_working: "works" });
  assert.equal(e.what_it_does, "does X");
  assert.equal(e.problem_solved, "");
  assert.equal(e.why_working, "works");
});

test("adaptGrowth: null when missing; keeps null pct_change, coerces null counts", () => {
  assert.equal(adaptGrowth(null), null);
  const g = adaptGrowth({ first_count: 3, latest_count: 11, weeks: 4, pct_change: 267 });
  assert.equal(g.pct_change, 267);
  const sparse = adaptGrowth({ first_count: null, latest_count: 5, weeks: 1, pct_change: null });
  assert.equal(sparse.pct_change, null);
  assert.equal(sparse.first_count, 0);
  assert.equal(sparse.latest_count, 5);
});

test("adaptHeroCreative: null when missing; s3_key → media url; type coerced", () => {
  assert.equal(adaptHeroCreative(null, media), null);
  const h = adaptHeroCreative({ type: "video", s3_key: "c/x.mp4", body_text: "b", title: "t" }, media);
  assert.equal(h.type, "video");
  assert.equal(h.snapshot_image_url, "/api/media/c/x.mp4");
  // no s3_key → no image url (UI falls back to a body_text tile)
  const noKey = adaptHeroCreative({ type: "image", s3_key: null, body_text: "just text", title: null }, media);
  assert.equal(noKey.snapshot_image_url, undefined);
  assert.equal(noKey.body_text, "just text");
});

test("adaptScalingPoint renames total_eu_reach → eu_total_reach", () => {
  const p = adaptScalingPoint({
    week_start: "2026-03-02", active_ad_count: 4,
    distinct_creative_count: 4, total_eu_reach: 400,
  });
  assert.equal(p.eu_total_reach, 400);
  assert.equal(p.active_ad_count, 4);
  assert.equal(p.distinct_creative_count, 4);
  // null reach → undefined (optional field), null counts → 0
  const q = adaptScalingPoint({
    week_start: "2026-03-09", active_ad_count: null,
    distinct_creative_count: null, total_eu_reach: null,
  });
  assert.equal(q.eu_total_reach, undefined);
  assert.equal(q.active_ad_count, 0);
});

test("adaptReviewRow fills active_ad_count / max_longevity_days the Review slip needs", () => {
  const r = adaptReviewRow({
    advertiser_id: 9, cohort: "Health & Fitness×US", page_name: "Bravo",
    app_name: null, gem_score: 72, active_ad_count: 4, max_longevity_days: 22,
  });
  assert.equal(r.active_ad_count, 4);
  assert.equal(r.max_longevity_days, 22);
  // older API build omits the two fields → coerced to 0, never undefined
  const legacy = adaptReviewRow({
    advertiser_id: 9, cohort: "Health & Fitness×US",
    page_name: "Bravo", app_name: null, gem_score: 72,
  });
  assert.equal(legacy.active_ad_count, 0);
  assert.equal(legacy.max_longevity_days, 0);
});

test("adaptDossier flattens the nested API shape and merges scaling", () => {
  const dossier = adaptDossier(
    {
      gem: {
        advertiser_id: 100, cohort: "Health & Fitness×US", page_name: "Alpha",
        app_id: 90211, app_name: "SleepWave", gem_score: 88,
        scaling_velocity: 0.9, under_radar: 0.86, funnel_intent: 0.78,
        active_ad_count: 47, max_longevity_days: 31, alert: true,
        refreshed_at: "2026-06-04T08:00:00Z",
      },
      page_id: "61557123456789",
      domain: "sleepwave.app",
      page_category: "Health/Wellness",
      ig_username: "sleepwave.app",
      ig_followers: 14200,
      parent_company: null,
      apps: [
        { store: "ios", store_id: "id90211", name: "SleepWave", developer: "SleepWave Inc", genre: "Health & Fitness", release_date: "2026-02-01", rating: 4.7, rating_count: 1234, is_emerging: true },
        { store: "web", store_id: "", name: null, developer: null, genre: null, release_date: null, rating: null, rating_count: null, is_emerging: null },
      ],
      funnels: [
        { landing_url: "https://sleepwave.app/tt", final_url_after_redirect: null, screenshot_s3_key: "funnels/abc/step1.png" },
        { landing_url: "https://sleepwave.app/quiz", final_url_after_redirect: "https://apps.apple.com/app/id90211", screenshot_s3_key: null },
      ],
      creatives: [
        { type: "video", s3_key: "creatives/deadbeef.mp4", body_text: "POV: your brain finally shuts up", title: "cant sleep" },
        { type: "image", s3_key: null, body_text: null, title: null },
      ],
    },
    [
      { week_start: "2026-05-04", active_ad_count: 6, distinct_creative_count: 3, total_eu_reach: 600 },
      { week_start: "2026-05-11", active_ad_count: 12, distinct_creative_count: 5, total_eu_reach: 1200 },
    ],
    [],
    (key) => `/api/media/${key}`,
  );

  // flattened ShortlistRow fields are at the top level
  assert.equal(dossier.advertiser_id, 100);
  assert.equal(dossier.app_name, "SleepWave");
  assert.equal(dossier.gem_score, 88);
  assert.equal(dossier.alert, true);

  // scaling merged + field-renamed
  assert.equal(dossier.scaling.length, 2);
  assert.equal(dossier.scaling[0].active_ad_count, 6);
  assert.equal(dossier.scaling[1].eu_total_reach, 1200);

  // creatives mapped: id from s3_key (fallback when null), type, text
  assert.equal(dossier.creatives.length, 2);
  assert.equal(dossier.creatives[0].id, "creatives/deadbeef.mp4");
  assert.equal(dossier.creatives[0].type, "video");
  assert.equal(dossier.creatives[0].body_text, "POV: your brain finally shuts up");
  assert.equal(dossier.creatives[1].id, "creative-1"); // null s3_key → index fallback
  assert.equal(dossier.creatives[1].type, "image");
  // s3_key → media proxy URL; null s3_key → no image (glyph fallback in UI)
  assert.equal(dossier.creatives[0].snapshot_image_url, "/api/media/creatives/deadbeef.mp4");
  assert.equal(dossier.creatives[1].snapshot_image_url, undefined);

  // funnel: step index 1-based, label from host, final_url from redirect
  assert.equal(dossier.funnel[0].step, 1);
  assert.equal(dossier.funnel[0].label, "sleepwave.app");
  assert.equal(dossier.funnel[0].screenshot_url, "/api/media/funnels/abc/step1.png");
  assert.equal(dossier.funnel[1].step, 2);
  assert.equal(dossier.funnel[1].label, "apps.apple.com");
  assert.equal(dossier.funnel[1].final_url, "https://apps.apple.com/app/id90211");
  assert.equal(dossier.funnel[1].screenshot_url, undefined); // null screenshot_s3_key

  // company: domain + page_category + IG + page_id mapped; still-unstored hidden
  assert.equal(dossier.company.domain, "sleepwave.app");
  assert.equal(dossier.company.page_category, "Health/Wellness");
  assert.equal(dossier.company.ig_username, "sleepwave.app");
  assert.equal(dossier.company.ig_followers, 14200);
  assert.equal(dossier.company.parent_company, null);
  assert.equal(dossier.company.page_id, "61557123456789");
  assert.equal(dossier.company.tech, undefined); // not yet exposed (iteration 2)
  assert.equal(dossier.company.domain_age_years, undefined);

  // apps: known stores kept; unknown store / empty id dropped
  assert.equal(dossier.apps.length, 1);
  assert.equal(dossier.apps[0].store, "ios");
  assert.equal(dossier.apps[0].store_id, "id90211");
  assert.equal(dossier.apps[0].name, "SleepWave");
  // store-listing fields are carried, no longer dropped
  assert.equal(dossier.apps[0].developer, "SleepWave Inc");
  assert.equal(dossier.apps[0].genre, "Health & Fitness");
  assert.equal(dossier.apps[0].release_date, "2026-02-01");
  assert.equal(dossier.apps[0].rating, 4.7);
  assert.equal(dossier.apps[0].rating_count, 1234);
  assert.equal(dossier.apps[0].is_emerging, true);
  // store-preview fields absent on this build → undefined (graceful)
  assert.equal(dossier.apps[0].description, undefined);
  assert.equal(dossier.apps[0].icon_url, undefined);
  assert.equal(dossier.apps[0].screenshots, undefined);
  assert.equal(dossier.apps[0].installs, undefined);
  assert.equal(dossier.apps[0].updated_at, undefined);
  assert.equal(dossier.apps[0].reviews, undefined);

  // tagline has no server source → empty string (renders blank, no crash)
  assert.equal(dossier.tagline, "");

  // new row-level fields absent on the dossier gem → null (inherited)
  assert.equal(dossier.explainer, null);
  assert.equal(dossier.growth, null);
  assert.equal(dossier.hero_creative, null);

  // ad-library fields absent on older builds → undefined; app_metrics merged []
  assert.deepEqual(dossier.app_metrics, []);
  assert.equal(dossier.ads_last_seen, undefined);
  assert.equal(dossier.page_like_count, undefined);
  assert.equal(dossier.meta_active_ads, undefined);
  assert.equal(dossier.meta_active_ads_capped, undefined);
  assert.equal(dossier.meta_active_ads_checked_at, undefined);
  // creative ad-meta fields absent on older builds → undefined
  assert.equal(dossier.creatives[0].ad_started_on, undefined);
  assert.equal(dossier.creatives[0].ad_days_running, undefined);
  assert.equal(dossier.creatives[0].publisher_platforms, undefined);
  assert.equal(dossier.creatives[0].eu_total_reach, undefined);
});

test("adaptDossier carries the new signal fields: app preview, ad meta, library count", () => {
  const dossier = adaptDossier(
    {
      gem: {
        advertiser_id: 7, cohort: "Photo & Video×US", page_name: "Lensa",
        app_id: 555, app_name: "Lensa", gem_score: 81,
        scaling_velocity: 0.8, under_radar: 0.7, funnel_intent: 0.6,
        active_ad_count: 12, max_longevity_days: 40, alert: false,
        refreshed_at: "2026-06-09T08:00:00Z",
      },
      page_id: "123", domain: "lensa.app", page_category: null,
      ig_username: null, ig_followers: null, parent_company: null,
      apps: [
        {
          store: "android", store_id: "com.lensa.app", name: "Lensa",
          developer: "Lensa Labs", genre: "Photography", release_date: "2026-01-15",
          rating: 4.4, rating_count: 980, is_emerging: true,
          description: "Edit photos fast.",
          icon_url: "https://cdn.example/icon.png",
          screenshots: ["https://cdn.example/s1.png", "https://cdn.example/s2.png"],
          price_label: "Free",
          installs: 116000000,
          updated_at: "2026-06-09T07:00:00Z",
          reviews: [
            { author: "ana", rating: 5, title: "great", content: "love it", reviewed_at: "2026-06-08T10:00:00Z" },
            { author: "", rating: null, title: "", content: "meh", reviewed_at: null },
          ],
        },
      ],
      funnels: [],
      creatives: [
        {
          type: "image", s3_key: "creatives/x.jpg", body_text: "b", title: "t",
          external_ad_id: "924353860103615", cta_text: "Install now",
          link_url: "http://itunes.apple.com/app/id90211",
          ad_started_on: "2026-04-09", ad_active: true, ad_days_running: 62,
          publisher_platforms: ["FACEBOOK", "INSTAGRAM"], eu_total_reach: 120000,
        },
        {
          type: "text", s3_key: null, body_text: "ended ad copy", title: null,
          external_ad_id: "111", ad_active: false,
          ad_started_on: "2025-07-10", ad_ended_on: "2025-08-22", ad_days_running: 43,
        },
      ],
      ads_last_seen: "2026-06-09T06:00:00Z",
      page_like_count: 1530,
      meta_active_ads: 250,
      meta_active_ads_capped: true,
      meta_active_ads_checked_at: "2026-06-09T05:00:00Z",
    },
    [],
    [
      { app_id: 555, store: "android", snapshot_date: "2026-06-08", rating: 4.4, rating_count: 970, installs: 115000000 },
      { app_id: 555, store: "android", snapshot_date: "2026-06-09", rating: 4.4, rating_count: 980, installs: 116000000 },
    ],
    (key) => `/api/media/${key}`,
  );

  // app preview fields carried through, null-safe
  const app = dossier.apps[0];
  assert.equal(app.description, "Edit photos fast.");
  assert.equal(app.icon_url, "https://cdn.example/icon.png");
  assert.deepEqual(app.screenshots, ["https://cdn.example/s1.png", "https://cdn.example/s2.png"]);
  assert.equal(app.price_label, "Free");
  assert.equal(app.installs, 116000000);
  assert.equal(app.updated_at, "2026-06-09T07:00:00Z");
  assert.equal(app.reviews.length, 2);
  assert.equal(app.reviews[0].author, "ana");
  assert.equal(app.reviews[0].rating, 5);
  // empty strings / nulls in a review → undefined
  assert.equal(app.reviews[1].author, undefined);
  assert.equal(app.reviews[1].rating, undefined);
  assert.equal(app.reviews[1].title, undefined);
  assert.equal(app.reviews[1].content, "meh");
  assert.equal(app.reviews[1].reviewed_at, undefined);

  // per-creative ad meta
  const cr = dossier.creatives[0];
  assert.equal(cr.ad_started_on, "2026-04-09");
  assert.equal(cr.ad_active, true);
  assert.equal(cr.ad_days_running, 62);
  assert.deepEqual(cr.publisher_platforms, ["FACEBOOK", "INSTAGRAM"]);
  assert.equal(cr.eu_total_reach, 120000);
  // identity + CTA: id prefers external_ad_id over s3_key
  assert.equal(cr.id, "924353860103615");
  assert.equal(cr.external_ad_id, "924353860103615");
  assert.equal(cr.cta_text, "Install now");
  assert.equal(cr.link_url, "http://itunes.apple.com/app/id90211");
  // ended text-backstop ad: honest type, run range carried through
  const ended = dossier.creatives[1];
  assert.equal(ended.type, "text");
  assert.equal(ended.ad_active, false);
  assert.equal(ended.ad_started_on, "2025-07-10");
  assert.equal(ended.ad_ended_on, "2025-08-22");
  assert.equal(ended.ad_days_running, 43);

  // dossier-level ad-library + freshness fields
  assert.equal(dossier.ads_last_seen, "2026-06-09T06:00:00Z");
  assert.equal(dossier.page_like_count, 1530);
  assert.equal(dossier.meta_active_ads, 250);
  assert.equal(dossier.meta_active_ads_capped, true);
  assert.equal(dossier.meta_active_ads_checked_at, "2026-06-09T05:00:00Z");

  // app_metrics merged + null-safe
  assert.equal(dossier.app_metrics.length, 2);
  assert.equal(dossier.app_metrics[0].snapshot_date, "2026-06-08");
  assert.equal(dossier.app_metrics[1].installs, 116000000);
});

test("adaptAppMetricPoint coerces nulls to undefined", () => {
  const p = adaptAppMetricPoint({
    app_id: 9, store: "ios", snapshot_date: "2026-06-09",
    rating: 4.8, rating_count: 321, installs: null,
  });
  assert.equal(p.app_id, 9);
  assert.equal(p.store, "ios");
  assert.equal(p.snapshot_date, "2026-06-09");
  assert.equal(p.rating, 4.8);
  assert.equal(p.rating_count, 321);
  assert.equal(p.installs, undefined);
});
