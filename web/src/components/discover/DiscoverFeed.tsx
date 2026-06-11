"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { getCategories, getShortlist } from "@/lib/api";
import { Fallback } from "@/components/Fallback";
import { ActivationChecklist, EmptyState, Pagination } from "@/components/ui";
import { PAGE_SIZE, clampPage } from "@/lib/pagination";
import { shortlistParams } from "@/lib/query";
import type { CategoryCount, ShortlistQuery, ShortlistRow } from "@/types/contracts";
import { GemCard } from "./GemCard";
import { DEFAULT_FILTERS, FilterRail, type Filters } from "./FilterRail";

const GRID: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fill, minmax(230px, 1fr))",
  gap: 12,
  alignItems: "stretch",
};

/** The whole filter set + page lives in the URL (shareable, back-button-able);
 * defaults are omitted so the bare /discover stays clean. */
function readUrl(sp: URLSearchParams): { filters: Filters; page: number } {
  const num = (key: string) => {
    const n = Number(sp.get(key));
    return Number.isFinite(n) && n > 0 ? Math.trunc(n) : 0;
  };
  const sort = sp.get("sort");
  const age = num("age");
  return {
    filters: {
      view: sp.get("view") === "all" ? "all" : "rising",
      sort: sort === "fastest" || sort === "newest" ? sort : "gemscore",
      category: sp.get("category") ?? "",
      q: (sp.get("q") ?? "").trim(),
      minScore: Math.min(95, num("score")),
      hasFunnel: sp.get("funnel") === "1",
      minAdAge: [14, 21, 30].includes(age) ? age : 0,
    },
    // Capped so a hand-mangled page param can't overflow the offset the API
    // accepts; anything past the real last page walks back after the fetch.
    page: Math.min(Math.max(1, num("page")), 9999),
  };
}

function writeUrl(f: Filters, page: number): string {
  const sp = new URLSearchParams();
  if (f.view !== "rising") sp.set("view", f.view);
  if (f.q) sp.set("q", f.q);
  if (f.category) sp.set("category", f.category);
  if (f.sort !== "gemscore") sp.set("sort", f.sort);
  if (f.minScore > 0) sp.set("score", String(f.minScore));
  if (f.minAdAge > 0) sp.set("age", String(f.minAdAge));
  if (f.hasFunnel) sp.set("funnel", "1");
  if (page > 1) sp.set("page", String(page));
  return sp.toString();
}

function SkeletonGrid() {
  return (
    <div style={GRID} role="status" aria-label="Loading gems">
      {Array.from({ length: 8 }, (_, i) => (
        <div key={i} style={{ border: "1px solid var(--line)", background: "var(--paper)" }}>
          <div className="skel" style={{ aspectRatio: "16 / 10" }} />
          <div style={{ padding: 12, display: "flex", flexDirection: "column", gap: 8 }}>
            <div className="skel" style={{ height: 14, width: "70%" }} />
            <div className="skel" style={{ height: 10, width: "45%" }} />
          </div>
        </div>
      ))}
    </div>
  );
}

/** One resolved /shortlist page, tagged with the query string and page that
 * fetched it. Comparing the tag against the live query string derives the
 * load phases, and the grid + pager render entirely from one result's epoch
 * so stale rows never wear fresh numbers. */
interface FeedResult {
  rows: ShortlistRow[];
  total: number;
  key: string;
  page: number;
}

export function DiscoverFeed() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const { filters, page } = useMemo(() => readUrl(searchParams), [searchParams]);

  const [result, setResult] = useState<FeedResult | null>(null);
  const [error, setError] = useState(false);
  const [categories, setCategories] = useState<CategoryCount[]>([]);
  const [reload, setReload] = useState(0);

  const query: ShortlistQuery = useMemo(
    () => ({
      category: filters.category || undefined,
      q: filters.q || undefined,
      min_score: filters.minScore || undefined,
      rising: filters.view === "rising",
      has_funnel: filters.hasFunnel || undefined,
      min_ad_age: filters.minAdAge || undefined,
      sort: filters.sort,
      limit: PAGE_SIZE,
      offset: (page - 1) * PAGE_SIZE,
    }),
    [filters, page],
  );
  const queryKey = useMemo(() => shortlistParams(query).toString(), [query]);

  useEffect(() => {
    const ctrl = new AbortController();
    getShortlist(query, ctrl.signal)
      .then(({ rows, total }) => {
        if (ctrl.signal.aborted) return;
        setResult({
          rows,
          total,
          key: shortlistParams(query).toString(),
          page: (query.offset ?? 0) / PAGE_SIZE + 1,
        });
        setError(false);
      })
      .catch(() => {
        if (!ctrl.signal.aborted) setError(true);
      });
    return () => ctrl.abort();
  }, [query, reload]);

  // Category options carry counts under the CURRENT filters (minus the
  // category itself), so every number matches what picking it would yield.
  // A failure here only costs the options.
  const categoriesQuery: ShortlistQuery = useMemo(
    () => ({
      q: filters.q || undefined,
      min_score: filters.minScore || undefined,
      rising: filters.view === "rising",
      has_funnel: filters.hasFunnel || undefined,
      min_ad_age: filters.minAdAge || undefined,
    }),
    [filters],
  );
  useEffect(() => {
    let alive = true;
    getCategories(categoriesQuery)
      .then((c) => {
        if (alive) setCategories(c);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [categoriesQuery]);

  const navigate = useCallback(
    (f: Filters, p: number) => {
      const qs = writeUrl(f, p);
      router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    },
    [router, pathname],
  );

  // Any filter edit restarts at page 1; an explicit page change scrolls back up.
  const setFilters = useCallback((f: Filters) => navigate(f, 1), [navigate]);
  const setPage = useCallback(
    (p: number) => {
      navigate(filters, p);
      const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      window.scrollTo({ top: 0, behavior: still ? "auto" : "smooth" });
    },
    [navigate, filters],
  );

  // Phases, derived: nothing yet → skeletons; a result for an older query →
  // stale grid dims while the new one is in flight; fresh result → ready.
  const fresh = result !== null && result.key === queryKey;
  const refreshing = !error && result !== null && !fresh;
  const rows = result?.rows ?? [];
  const total = result?.total ?? 0;

  // When a narrowed result set strands the page past the end, walk back to the
  // last page that exists.
  useEffect(() => {
    if (error || result === null || result.key !== queryKey) return;
    const last = clampPage(page, result.total);
    if (page > last) navigate(filters, last);
  }, [error, result, queryKey, page, filters, navigate]);

  const retry = () => {
    setError(false);
    setResult(null);
    setReload((n) => n + 1);
  };

  // Only filters that can NARROW the result set (view=all is broader than the
  // rising default, and sort never changes the count).
  const narrowed =
    !!filters.category || !!filters.q || filters.minScore > 0 || filters.hasFunnel || filters.minAdAge > 0;

  return (
    <main style={{ maxWidth: 1200, margin: "0 auto", padding: "24px 22px 80px" }}>
      <header style={{ marginBottom: 20 }}>
        <h1 className="t-2xl w-heavy" style={{ margin: 0 }}>
          {filters.view === "rising" ? "Gems rising now" : "All gems"}
        </h1>
        <p className="t-sm" aria-live="polite" style={{ color: "var(--muted)", marginTop: 6 }}>
          {error
            ? "The feed didn't load."
            : result === null
              ? "Loading…"
              : refreshing
                ? "Updating…"
                : `${total} ${total === 1 ? "gem" : "gems"} found.${filters.view === "rising" ? " Falling ad counts hidden." : ""}`}
        </p>
      </header>

      {!error && result !== null && rows.length > 0 && <ActivationChecklist />}

      {!error && (
        <div style={{ marginBottom: 22 }}>
          <FilterRail
            filters={filters}
            categories={categories}
            onChange={setFilters}
            onReset={() => setFilters(DEFAULT_FILTERS)}
          />
          {/* In-flight indicator; keeps its 2px so the grid never jumps. */}
          <div className="feed-scanbar" aria-hidden style={{ marginTop: 12, visibility: refreshing ? "visible" : "hidden" }} />
        </div>
      )}

      {error ? (
        <Fallback message="The feed didn't load. Check that the server is running, then try again." onRetry={retry} />
      ) : result === null ? (
        <SkeletonGrid />
      ) : fresh && total === 0 ? (
        narrowed ? (
          <EmptyState title="Nothing matches" hint="Filters are too tight. Loosen them." ctaLabel="Reset" onCta={() => setFilters(DEFAULT_FILTERS)} />
        ) : filters.view === "rising" ? (
          <EmptyState
            title="Nothing rising right now"
            hint="Falling ad counts are hidden in this view."
            ctaLabel="Show all gems"
            onCta={() => setFilters({ ...filters, view: "all" })}
          />
        ) : (
          <EmptyState
            title="No gems yet"
            hint="Still scanning Meta ads. Check back soon — gems will show up here."
            ctaLabel="Look again"
            onCta={retry}
          />
        )
      ) : (
        // The grid and pager render from the RESULT's epoch (its page/total),
        // dimmed and inert while a newer query is in flight — stale rows never
        // pose as the new answer, and keyboard/AT can't operate them.
        <div className={`feed-grid${refreshing ? " feed-stale" : ""}`} inert={refreshing || undefined} aria-busy={refreshing}>
          {rows.length > 0 && (
            <>
              <div key={result.key} style={GRID}>
                {rows.map((row, i) => (
                  <div key={row.advertiser_id} className="gem-enter" style={{ display: "grid", animationDelay: `${Math.min(i, 11) * 24}ms` }}>
                    <GemCard row={row} />
                  </div>
                ))}
              </div>
              <Pagination
                page={result.page}
                pageCount={Math.max(1, Math.ceil(result.total / PAGE_SIZE))}
                total={result.total}
                rangeStart={(result.page - 1) * PAGE_SIZE + 1}
                rangeEnd={(result.page - 1) * PAGE_SIZE + rows.length}
                onPage={setPage}
              />
            </>
          )}
        </div>
      )}
    </main>
  );
}
