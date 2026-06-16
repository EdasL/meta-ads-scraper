/**
 * Pure query-string building for GET /api/shortlist — no React, no fetch
 * (unit-tested in `query.test.ts`, run with `node --test`). Unset axes and
 * server defaults are omitted so URLs stay short and shareable.
 */
import type { ShortlistQuery, ShortlistSort, SortDir } from "@/types/contracts";

/** Each sort key's natural direction — what the server uses when no dir is
 * sent, and what the first click on a table header means. */
export const SORT_DEFAULT_DIR: Record<ShortlistSort, SortDir> = {
  gemscore: "desc",
  fastest: "desc",
  newest: "asc",
};

export function shortlistParams(query: ShortlistQuery): URLSearchParams {
  const params = new URLSearchParams();
  if (query.cohort) params.set("cohort", query.cohort);
  if (query.category) params.set("category", query.category);
  if (query.q) params.set("q", query.q);
  if (query.min_score) params.set("min_score", String(query.min_score));
  if (query.has_funnel) params.set("has_funnel", "true");
  if (query.min_ad_age) params.set("min_ad_age", String(query.min_ad_age));
  if (query.incubating) params.set("incubating", "true");
  if (query.sort && query.sort !== "gemscore") params.set("sort", query.sort);
  if (query.dir && query.dir !== SORT_DEFAULT_DIR[query.sort ?? "gemscore"]) params.set("dir", query.dir);
  if (query.limit != null) params.set("limit", String(query.limit));
  if (query.offset) params.set("offset", String(query.offset));
  return params;
}
