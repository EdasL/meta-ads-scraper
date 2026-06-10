/**
 * Pure page math for the Discover grid's numbered pagination — no React
 * (unit-tested in `pagination.test.ts`, run with `node --test`).
 */

/** Gems per Discover page. */
export const PAGE_SIZE = 24;

export type PageItem = number | "gap";

/**
 * The visible page numbers: first and last page always, a window of ±1 around
 * the current page, and "gap" where pages are elided. A gap of exactly one
 * page shows that page instead (an ellipsis would be longer than the number).
 *
 *   pageItems(6, 12) → [1, "gap", 5, 6, 7, "gap", 12]
 */
export function pageItems(page: number, pageCount: number): PageItem[] {
  if (pageCount < 1) return [];
  const wanted = new Set([1, pageCount, page - 1, page, page + 1]);
  const pages = [...wanted].filter((p) => p >= 1 && p <= pageCount).sort((a, b) => a - b);
  const items: PageItem[] = [];
  let prev = 0;
  for (const p of pages) {
    if (p - prev === 2) items.push(prev + 1);
    else if (p - prev > 2) items.push("gap");
    items.push(p);
    prev = p;
  }
  return items;
}

/** Clamps a 1-based page so it always points at an existing page. */
export function clampPage(page: number, total: number, pageSize: number = PAGE_SIZE): number {
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  return Math.min(Math.max(1, page), pageCount);
}
