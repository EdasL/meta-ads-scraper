"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import type { FunnelRow, FunnelSort, SortDir } from "@/types/contracts";
import { FUNNEL_SORT_DEFAULT_DIR } from "@/lib/query";
import { getFunnels } from "@/lib/api";
import { PAGE_SIZE, clampPage } from "@/lib/pagination";
import { EmptyState } from "@/components/ui";
import { Pagination } from "@/components/ui";
import { FunnelTable } from "./FunnelTable";

/** Funnel filter state — the search box, the quiz-only toggle, and the sort. */
interface FunnelFilters {
  q: string;
  hasQuiz: boolean;
  sort: FunnelSort;
  dir: SortDir;
}

const DEFAULT_FILTERS: FunnelFilters = {
  q: "",
  hasQuiz: false,
  sort: "newest",
  dir: FUNNEL_SORT_DEFAULT_DIR.newest,
};

const VALID_SORTS: FunnelSort[] = ["newest", "advertisers", "seen"];

interface FeedResult {
  rows: FunnelRow[];
  total: number;
  key: string;
  page: number;
}

/** Parse the URL into filters + page. Mirrors DiscoverFeed's readUrl: the URL
 * is the single source of truth, so a shared link reproduces the exact view. */
function readUrl(params: URLSearchParams): { filters: FunnelFilters; page: number } {
  const sortRaw = params.get("sort");
  const sort = VALID_SORTS.includes(sortRaw as FunnelSort) ? (sortRaw as FunnelSort) : DEFAULT_FILTERS.sort;
  const dirRaw = params.get("dir");
  const dir: SortDir = dirRaw === "asc" || dirRaw === "desc" ? dirRaw : FUNNEL_SORT_DEFAULT_DIR[sort];
  const pageRaw = Number(params.get("page"));
  return {
    filters: {
      q: params.get("q") ?? "",
      hasQuiz: params.get("has_quiz") === "true",
      sort,
      dir,
    },
    page: Number.isFinite(pageRaw) && pageRaw > 1 ? Math.floor(pageRaw) : 1,
  };
}

/** Serialize filters + page back to a query string, omitting defaults so the
 * canonical "newest, page 1, no search" view has a clean bare URL. */
function writeUrl(filters: FunnelFilters, page: number): string {
  const params = new URLSearchParams();
  if (filters.q) params.set("q", filters.q);
  if (filters.hasQuiz) params.set("has_quiz", "true");
  if (filters.sort !== DEFAULT_FILTERS.sort) params.set("sort", filters.sort);
  if (filters.dir !== FUNNEL_SORT_DEFAULT_DIR[filters.sort]) params.set("dir", filters.dir);
  if (page > 1) params.set("page", String(page));
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

export function FunnelsFeed() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { filters, page } = useMemo(() => readUrl(searchParams), [searchParams]);

  const queryKey = useMemo(() => writeUrl(filters, page), [filters, page]);

  const [result, setResult] = useState<FeedResult | null>(null);
  const [error, setError] = useState(false);
  const [reload, setReload] = useState(0);
  const [searchText, setSearchText] = useState(filters.q);

  // Keep the search box in sync when the URL changes from outside (back/forward).
  useEffect(() => {
    setSearchText(filters.q);
  }, [filters.q]);

  function navigate(nextFilters: FunnelFilters, nextPage: number) {
    router.replace(writeUrl(nextFilters, nextPage), { scroll: false });
  }

  const setFilters = useCallback(
    (patch: Partial<FunnelFilters>) => {
      navigate({ ...filters, ...patch }, 1); // any filter change resets to page 1
    },
    [filters],
  );

  function setPage(next: number) {
    navigate(filters, next);
    if (typeof window !== "undefined") {
      const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
      window.scrollTo({ top: 0, behavior: reduce ? "auto" : "smooth" });
    }
  }

  // Debounce the search box into the URL (matches DiscoverFeed's 300ms).
  useEffect(() => {
    if (searchText === filters.q) return;
    const t = setTimeout(() => setFilters({ q: searchText }), 300);
    return () => clearTimeout(t);
  }, [searchText, filters.q, setFilters]);

  useEffect(() => {
    const controller = new AbortController();
    setError(false);
    getFunnels(
      {
        q: filters.q || undefined,
        has_quiz: filters.hasQuiz || undefined,
        sort: filters.sort,
        dir: filters.dir,
        limit: PAGE_SIZE,
        offset: (page - 1) * PAGE_SIZE,
      },
      controller.signal,
    )
      .then((res) => setResult({ rows: res.rows, total: res.total, key: queryKey, page }))
      .catch((err) => {
        if (!controller.signal.aborted) setError(true);
        void err;
      });
    return () => controller.abort();
  }, [filters.q, filters.hasQuiz, filters.sort, filters.dir, page, queryKey, reload]);

  // If a deep-linked page is now out of range (data shrank), step back in.
  useEffect(() => {
    if (!result) return;
    const clamped = clampPage(page, result.total, PAGE_SIZE);
    if (clamped !== page) navigate(filters, clamped);
  }, [result, page, filters]);

  const fresh = result?.key === queryKey;
  const rows = result?.rows ?? [];
  const total = result?.total ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  function onSort(sort: FunnelSort, dir: SortDir) {
    setFilters({ sort, dir });
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <FunnelFilterBar
        searchText={searchText}
        onSearch={setSearchText}
        hasQuiz={filters.hasQuiz}
        onToggleQuiz={(v) => setFilters({ hasQuiz: v })}
      />

      {error ? (
        <EmptyState
          title="Couldn't load funnels"
          hint="The API didn't respond. Check that the backend is running, then retry."
          ctaLabel="Retry"
          onCta={() => setReload((n) => n + 1)}
        />
      ) : result && rows.length === 0 ? (
        <EmptyState
          title={filters.q || filters.hasQuiz ? "No funnels match these filters" : "No confirmed funnels yet"}
          hint={
            filters.q || filters.hasQuiz
              ? "Clear the search or quiz filter to see the full list."
              : "Funnels appear here once the pipeline confirms a landing page rendered."
          }
          ctaLabel={filters.q || filters.hasQuiz ? "Clear filters" : undefined}
          onCta={filters.q || filters.hasQuiz ? () => navigate(DEFAULT_FILTERS, 1) : undefined}
        />
      ) : (
        <div style={{ opacity: fresh ? 1 : 0.6, transition: "opacity 120ms" }}>
          <FunnelTable rows={rows} sort={filters.sort} dir={filters.dir} onSort={onSort} />
          <Pagination
            page={page}
            pageCount={pageCount}
            total={total}
            rangeStart={total === 0 ? 0 : (page - 1) * PAGE_SIZE + 1}
            rangeEnd={Math.min(page * PAGE_SIZE, total)}
            onPage={setPage}
          />
        </div>
      )}
    </div>
  );
}

/** Search + quiz-only toggle. Deliberately lighter than the Discover FilterRail:
 * funnels have far fewer axes to filter on. */
function FunnelFilterBar({
  searchText,
  onSearch,
  hasQuiz,
  onToggleQuiz,
}: {
  searchText: string;
  onSearch: (v: string) => void;
  hasQuiz: boolean;
  onToggleQuiz: (v: boolean) => void;
}) {
  return (
    <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
      <input
        type="search"
        value={searchText}
        onChange={(e) => onSearch(e.target.value)}
        placeholder="Search landing domain or advertiser"
        aria-label="Search funnels"
        style={{
          flex: "1 1 280px",
          minWidth: 0,
          padding: "8px 12px",
          border: "1px solid var(--line)",
          borderRadius: 8,
          background: "var(--surface-1)",
          color: "var(--ink)",
          fontSize: 14,
        }}
      />
      <label style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 14, color: "var(--ink)" }}>
        <input type="checkbox" checked={hasQuiz} onChange={(e) => onToggleQuiz(e.target.checked)} />
        Quiz funnels only
      </label>
    </div>
  );
}
