"use client";

import { useState } from "react";
import { IconAdjustmentsHorizontal } from "@tabler/icons-react";

/**
 * Short, opinionated filter bar (spec §5.1). Default view = "rising"; advanced
 * axes hidden behind "more filter". ≤6 axes — never an enterprise filter wall.
 */
export type View = "rising" | "all";
export type Sort = "gemscore" | "fastest" | "newest";

export interface Filters {
  view: View;
  sort: Sort;
  cohort: string;
  minScore: number;
  hasFunnel: boolean;
  minAdAge: number;
}

export const DEFAULT_FILTERS: Filters = {
  view: "rising",
  sort: "gemscore",
  cohort: "",
  minScore: 0,
  hasFunnel: false,
  minAdAge: 0,
};

const SORT_LABEL: Record<Sort, string> = {
  gemscore: "Best score",
  fastest: "Fastest climb",
  newest: "Newest",
};

const VIEW_LABEL: Record<View, string> = {
  rising: "Rising",
  all: "All",
};

const control: React.CSSProperties = {
  background: "var(--surface-3)",
  border: "1px solid var(--line)",
  color: "var(--ink)",
  fontFamily: "var(--font-mono), monospace",
  fontSize: "var(--fs-xs)",
  padding: "6px 8px",
  outline: "none",
};

function Segmented({ view, onChange }: { view: View; onChange: (v: View) => void }) {
  return (
    <div style={{ display: "inline-flex", border: "1px solid var(--ink)" }}>
      {(["rising", "all"] as View[]).map((v) => (
        <button
          key={v}
          type="button"
          onClick={() => onChange(v)}
          className="font-mono"
          style={{
            cursor: "pointer",
            border: "none",
            padding: "6px 12px",
            fontSize: "var(--fs-xs)",
            background: view === v ? "var(--ink)" : "transparent",
            color: view === v ? "var(--bg)" : "var(--ink)",
          }}
        >
          {VIEW_LABEL[v]}
        </button>
      ))}
    </div>
  );
}

export function FilterRail({
  filters,
  cohorts,
  onChange,
  onReset,
}: {
  filters: Filters;
  cohorts: string[];
  onChange: (f: Filters) => void;
  onReset: () => void;
}) {
  const [open, setOpen] = useState(false);
  const set = (patch: Partial<Filters>) => onChange({ ...filters, ...patch });
  const advancedActive = !!filters.cohort || filters.minScore > 0 || filters.hasFunnel || filters.minAdAge > 0;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 12 }}>
        <Segmented view={filters.view} onChange={(v) => set({ view: v })} />

        <label className="eyebrow" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          Sort
          <select value={filters.sort} onChange={(e) => set({ sort: e.target.value as Sort })} style={control}>
            {(Object.keys(SORT_LABEL) as Sort[]).map((s) => (
              <option key={s} value={s}>{SORT_LABEL[s]}</option>
            ))}
          </select>
        </label>

        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          className="font-mono"
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            cursor: "pointer",
            background: advancedActive ? "var(--ink)" : "transparent",
            color: advancedActive ? "var(--bg)" : "var(--ink)",
            border: "1px solid var(--ink)",
            padding: "6px 10px",
            fontSize: "var(--fs-xs)",
          }}
        >
          <IconAdjustmentsHorizontal size={14} stroke={1.75} />
          More filters
        </button>

        {advancedActive && (
          <button
            type="button"
            onClick={onReset}
            className="font-mono"
            style={{ background: "transparent", border: "none", cursor: "pointer", color: "var(--muted)", fontSize: "var(--fs-xs)", textDecoration: "underline" }}
          >
            Clear
          </button>
        )}
      </div>

      {open && (
        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            gap: 18,
            alignItems: "center",
            padding: "14px 16px",
            border: "1px solid var(--line)",
            background: "var(--surface-1)",
          }}
        >
          <label className="eyebrow" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            Kind
            <select value={filters.cohort} onChange={(e) => set({ cohort: e.target.value })} style={control}>
              <option value="">All</option>
              {cohorts.map((c) => (
                <option key={c} value={c}>{c}</option>
              ))}
            </select>
          </label>

          <label className="eyebrow" style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
            Score ≥ <span className="nums" style={{ color: "var(--ink)", minWidth: 20 }}>{filters.minScore}</span>
            <input
              type="range"
              min={0}
              max={95}
              step={5}
              value={filters.minScore}
              onChange={(e) => set({ minScore: Number(e.target.value) })}
              style={{ accentColor: "var(--ink)" }}
            />
          </label>

          <label className="eyebrow" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            Ads running ≥
            <select value={filters.minAdAge} onChange={(e) => set({ minAdAge: Number(e.target.value) })} style={control}>
              {[0, 14, 21, 30].map((d) => (
                <option key={d} value={d}>{d === 0 ? "Any" : `${d} days`}</option>
              ))}
            </select>
          </label>

          <label className="eyebrow" style={{ display: "inline-flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
            <input type="checkbox" checked={filters.hasFunnel} onChange={(e) => set({ hasFunnel: e.target.checked })} style={{ accentColor: "var(--ink)" }} />
            Real funnel only
          </label>
        </div>
      )}
    </div>
  );
}
