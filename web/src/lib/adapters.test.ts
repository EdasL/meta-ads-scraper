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
  adaptReviewRow,
  adaptDossier,
} from "./adapters.ts";

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
  });
  assert.equal(row.page_name, "");
  assert.equal(row.scaling_velocity, 0);
  assert.equal(row.under_radar, 0);
  assert.equal(row.funnel_intent, 0);
  assert.equal(row.active_ad_count, 0);
  assert.equal(row.max_longevity_days, 0);
  assert.equal(row.alert, true);
  assert.equal(row.refreshed_at, "");
  assert.equal(row.gem_score, 88);
});

test("adaptShortlistRow passes real values through unchanged", () => {
  const row = adaptShortlistRow({
    advertiser_id: 1, cohort: "Education×US", page_name: "Alpha",
    app_id: 7, app_name: "AlphaApp", gem_score: 74.5,
    scaling_velocity: 0.9, under_radar: 0.8, funnel_intent: 0.7,
    active_ad_count: 6, max_longevity_days: 30, alert: false,
    refreshed_at: "2026-06-04T08:00:00Z",
  });
  assert.equal(row.app_id, 7);
  assert.equal(row.scaling_velocity, 0.9);
  assert.equal(row.alert, false);
  assert.equal(row.refreshed_at, "2026-06-04T08:00:00Z");
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
      domain: "sleepwave.app",
      page_category: "Health/Wellness",
      ig_username: "sleepwave.app",
      ig_followers: 14200,
      parent_company: null,
      apps: [],
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

  // company: domain + page_category + IG mapped; still-unstored fields hidden
  assert.equal(dossier.company.domain, "sleepwave.app");
  assert.equal(dossier.company.page_category, "Health/Wellness");
  assert.equal(dossier.company.ig_username, "sleepwave.app");
  assert.equal(dossier.company.ig_followers, 14200);
  assert.equal(dossier.company.parent_company, null);
  assert.equal(dossier.company.tech, undefined); // not yet exposed (iteration 2)
  assert.equal(dossier.company.domain_age_years, undefined);

  // tagline has no server source → empty string (renders blank, no crash)
  assert.equal(dossier.tagline, "");
});
