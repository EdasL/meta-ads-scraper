/**
 * Pure query-string building for GET /api/shortlist — no React, no fetch
 * (unit-tested in `query.test.ts`, run with `node --test`). Unset axes and
 * server defaults are omitted so URLs stay short and shareable.
 */
import type { ShortlistQuery } from "@/types/contracts";

export function shortlistParams(query: ShortlistQuery): URLSearchParams {
  const params = new URLSearchParams();
  if (query.cohort) params.set("cohort", query.cohort);
  if (query.category) params.set("category", query.category);
  if (query.q) params.set("q", query.q);
  if (query.min_score) params.set("min_score", String(query.min_score));
  if (query.rising) params.set("rising", "true");
  if (query.has_funnel) params.set("has_funnel", "true");
  if (query.min_ad_age) params.set("min_ad_age", String(query.min_ad_age));
  if (query.sort && query.sort !== "gemscore") params.set("sort", query.sort);
  if (query.limit != null) params.set("limit", String(query.limit));
  if (query.offset) params.set("offset", String(query.offset));
  return params;
}
