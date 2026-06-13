"use client";

import { useEffect, useRef, useState } from "react";
import { IconAdjustmentsHorizontal, IconSearch, IconX } from "@tabler/icons-react";
import { Dropdown } from "@/components/ui";
import { SORT_DEFAULT_DIR } from "@/lib/query";
import type { CategoryCount, ShortlistSort, SortDir } from "@/types/contracts";

/**
 * Short, opinionated filter bar (spec §5.1). Default view = "rising"; advanced
 * axes hidden behind "more filter". ≤6 axes — never an enterprise filter wall.
 * Every axis is applied server-side (GET /api/shortlist); this component only
 * edits the filter set.
 */
export type View = "rising" | "all" | "fresh";
export type Sort = ShortlistSort;

export interface Filters {
  view: View;
  sort: Sort;
  /** Sort direction; the table headers cycle natural → reversed → default. */
  dir: SortDir;
  /** App category — a real store genre from /api/shortlist/categories. */
  category: string;
  /** Search of app and page names. */
  q: string;
  minScore: number;
  hasFunnel: boolean;
  minAdAge: number;
}

export const DEFAULT_FILTERS: Filters = {
  view: "rising",
  sort: "gemscore",
  dir: "desc",
  category: "",
  q: "",
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
  fresh: "Fresh",
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
      {(["rising", "all", "fresh"] as View[]).map((v) => (
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

/**
 * Debounced name search. Local text state keeps typing instant; the committed
 * value only changes after a 300ms pause (each commit is a backend query).
 */
function SearchBox({ value, onCommit }: { value: string; onCommit: (q: string) => void }) {
  const [text, setText] = useState(value);
  const inputRef = useRef<HTMLInputElement>(null);

  // What our own debounce last committed: its echo coming back through the
  // URL round-trip must NOT clobber keystrokes typed in the meantime. State
  // (not a ref) because the comparison runs during render.
  const [lastCommit, setLastCommit] = useState(value);
  // Latest onCommit without re-arming the debounce on every parent render.
  const commitRef = useRef(onCommit);
  useEffect(() => {
    commitRef.current = onCommit;
  });
  const commitNow = (q: string) => {
    setLastCommit(q);
    commitRef.current(q);
  };

  // Follow OUTSIDE changes only (reset, back/forward nav) — the sanctioned
  // adjust-state-during-render pattern, not an effect.
  const [prevValue, setPrevValue] = useState(value);
  if (prevValue !== value) {
    setPrevValue(value);
    if (value !== lastCommit) {
      setLastCommit(value);
      setText(value);
    }
  }

  useEffect(() => {
    if (text.trim() === value) return;
    const id = setTimeout(() => commitNow(text.trim()), 300);
    return () => clearTimeout(id);
  }, [text, value]);

  return (
    <span style={{ position: "relative", display: "inline-flex", alignItems: "center" }}>
      <IconSearch
        size={13}
        stroke={1.75}
        aria-hidden
        style={{ position: "absolute", left: 8, color: "var(--muted)", pointerEvents: "none" }}
      />
      <input
        ref={inputRef}
        type="text"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") commitNow(text.trim());
          if (e.key === "Escape" && text) {
            setText("");
            commitNow("");
          }
        }}
        placeholder="Search apps and pages"
        aria-label="Search apps and pages"
        // `control` carries outline:none for its original consumers; an inline
        // outline would defeat the global :focus-visible ring (WCAG 2.4.7).
        style={{ ...control, outline: undefined, width: 200, paddingLeft: 26, paddingRight: text ? 26 : 8 }}
      />
      {text && (
        <button
          type="button"
          aria-label="Clear search"
          onClick={() => {
            setText("");
            commitNow("");
            // The button unmounts on clear — hand focus back to the input so
            // keyboard users aren't dropped to <body>.
            inputRef.current?.focus();
          }}
          style={{
            position: "absolute",
            right: 2,
            display: "inline-flex",
            background: "transparent",
            border: "none",
            padding: 4,
            cursor: "pointer",
            color: "var(--muted)",
          }}
        >
          <IconX size={13} stroke={1.75} />
        </button>
      )}
    </span>
  );
}

export function FilterRail({
  filters,
  categories,
  onChange,
  onReset,
}: {
  filters: Filters;
  categories: CategoryCount[];
  onChange: (f: Filters) => void;
  onReset: () => void;
}) {
  const [open, setOpen] = useState(false);
  const set = (patch: Partial<Filters>) => onChange({ ...filters, ...patch });
  const advancedActive = filters.minScore > 0 || filters.hasFunnel || filters.minAdAge > 0;
  const anyActive = advancedActive || !!filters.category || !!filters.q;

  const categoryOptions = [
    { value: "", label: "All categories" },
    ...categories.map((c) => ({ value: c.category, label: c.category, hint: String(c.count) })),
  ];
  // A category from the URL that the (still-loading or shrunken) list doesn't
  // carry yet must stay selectable — otherwise the dropdown would lie.
  if (filters.category && !categories.some((c) => c.category === filters.category)) {
    categoryOptions.push({ value: filters.category, label: filters.category });
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 12 }}>
        <Segmented view={filters.view} onChange={(v) => set({ view: v })} />

        <SearchBox value={filters.q} onCommit={(q) => set({ q })} />

        <Dropdown
          ariaLabel="Category"
          value={filters.category}
          options={categoryOptions}
          onChange={(category) => set({ category })}
          minWidth={170}
        />

        <label className="eyebrow" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          Sort
          <Dropdown
            ariaLabel="Sort"
            value={filters.sort}
            options={(Object.keys(SORT_LABEL) as Sort[]).map((s) => ({ value: s, label: SORT_LABEL[s] }))}
            onChange={(sort) => set({ sort: sort as Sort, dir: SORT_DEFAULT_DIR[sort as Sort] })}
            minWidth={130}
          />
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

        {anyActive && (
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
            <Dropdown
              ariaLabel="Ads running at least"
              value={String(filters.minAdAge)}
              options={[0, 14, 21, 30].map((d) => ({ value: String(d), label: d === 0 ? "Any" : `${d} days` }))}
              onChange={(v) => set({ minAdAge: Number(v) })}
              minWidth={110}
            />
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
