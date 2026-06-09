"use client";

import { useEffect, useMemo, useState } from "react";
import { getShortlist } from "@/lib/api";
import { statusChip } from "@/lib/why";
import { Fallback } from "@/components/Fallback";
import { ActivationChecklist, EmptyState } from "@/components/ui";
import type { ShortlistRow } from "@/types/contracts";
import { GemCard } from "./GemCard";
import { DEFAULT_FILTERS, FilterRail, type Filters, type Sort } from "./FilterRail";

const SORTERS: Record<Sort, (a: ShortlistRow, b: ShortlistRow) => number> = {
  gemscore: (a, b) => b.gem_score - a.gem_score,
  fastest: (a, b) => b.scaling_velocity - a.scaling_velocity,
  newest: (a, b) => a.max_longevity_days - b.max_longevity_days,
};

function applyFilters(rows: ShortlistRow[], f: Filters): ShortlistRow[] {
  let list = rows.slice();
  if (f.view === "rising") list = list.filter((r) => statusChip(r) !== "cooling");
  if (f.cohort) list = list.filter((r) => r.cohort === f.cohort);
  if (f.minScore > 0) list = list.filter((r) => r.gem_score >= f.minScore);
  if (f.hasFunnel) list = list.filter((r) => r.funnel_intent >= 0.5);
  if (f.minAdAge > 0) list = list.filter((r) => r.max_longevity_days >= f.minAdAge);
  return list.sort(SORTERS[f.sort]);
}

export function DiscoverFeed() {
  const [rows, setRows] = useState<ShortlistRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [reload, setReload] = useState(0);
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);

  useEffect(() => {
    let alive = true;
    getShortlist()
      .then((r) => {
        if (!alive) return;
        setRows(r);
        setError(false);
        setLoading(false);
      })
      .catch(() => {
        if (alive) {
          setError(true);
          setLoading(false);
        }
      });
    return () => {
      alive = false;
    };
  }, [reload]);

  const cohorts = useMemo(() => Array.from(new Set(rows.map((r) => r.cohort))), [rows]);
  const visible = useMemo(() => applyFilters(rows, filters), [rows, filters]);

  const retry = () => {
    setError(false);
    setLoading(true);
    setReload((n) => n + 1);
  };

  return (
    <main style={{ maxWidth: 1200, margin: "0 auto", padding: "24px 22px 80px" }}>
      <header style={{ marginBottom: 20 }}>
        <h1 className="t-2xl w-heavy caps" style={{ margin: 0 }}>
          {filters.view === "rising" ? "gems rising now" : "all gems"}
        </h1>
        <p className="t-sm" style={{ color: "var(--muted)", marginTop: 6 }}>
          {loading
            ? "grug look…"
            : `grug find ${visible.length} gem. ${filters.view === "rising" ? "the movers first." : "best score first."}`}
        </p>
      </header>

      {!error && !loading && rows.length > 0 && <ActivationChecklist />}

      {!error && !loading && (
        <div style={{ marginBottom: 22 }}>
          <FilterRail filters={filters} cohorts={cohorts} onChange={setFilters} onReset={() => setFilters(DEFAULT_FILTERS)} />
        </div>
      )}

      {error ? (
        <Fallback message="grug can not reach board. server sleeping?" onRetry={retry} />
      ) : loading ? (
        <div className="t-lg w-strong caps" style={{ padding: 48, color: "var(--muted)" }}>grug get board…</div>
      ) : rows.length === 0 ? (
        <EmptyState
          title="no gem yet"
          hint="grug still digging meta ads. come back soon — gem show up here."
          ctaLabel="look again"
          onCta={retry}
        />
      ) : visible.length === 0 ? (
        <EmptyState title="nothing match" hint="filter too tight. loosen it." ctaLabel="put back" onCta={() => setFilters(DEFAULT_FILTERS)} />
      ) : (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))",
            gap: 16,
            alignItems: "stretch",
          }}
        >
          {visible.map((row) => (
            <GemCard key={row.advertiser_id} row={row} />
          ))}
        </div>
      )}
    </main>
  );
}
