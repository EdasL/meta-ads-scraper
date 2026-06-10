"use client";

import { useMemo, useState } from "react";
import { useSavedList } from "@/lib/useSaved";
import { EmptyState } from "@/components/ui";
import { SavedCard } from "./SavedCard";
import { StageBoard } from "./StageBoard";
import { CompareView } from "./CompareView";

type View = "list" | "board" | "compare";

const page: React.CSSProperties = { maxWidth: 1100, margin: "0 auto", padding: "24px 22px 80px" };
const selectStyle: React.CSSProperties = {
  background: "var(--surface-3)",
  border: "1px solid var(--line)",
  color: "var(--ink)",
  fontFamily: "var(--font-mono), monospace",
  fontSize: "var(--fs-xs)",
  padding: "5px 8px",
};

const VIEW_LABEL: Record<View, string> = { list: "List", board: "Board", compare: "Compare" };

function ViewSwitch({ view, onChange }: { view: View; onChange: (v: View) => void }) {
  const VIEWS: View[] = ["list", "board", "compare"];
  return (
    <div style={{ display: "inline-flex", border: "1px solid var(--ink)" }}>
      {VIEWS.map((v) => (
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

export function SavedGems() {
  const saved = useSavedList();
  const [view, setView] = useState<View>("list");
  const [selected, setSelected] = useState<number[]>([]);
  const [tag, setTag] = useState("");

  const tags = useMemo(() => Array.from(new Set(saved.flatMap((g) => g.tags))).sort(), [saved]);
  const filtered = useMemo(() => (tag ? saved.filter((g) => g.tags.includes(tag)) : saved), [saved, tag]);
  const compareGems = useMemo(() => saved.filter((g) => selected.includes(g.advertiserId)).slice(0, 5), [saved, selected]);

  const toggleSelect = (id: number) =>
    setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : s.length < 5 ? [...s, id] : s));

  if (saved.length === 0) {
    return (
      <main style={page}>
        <h1 className="t-2xl w-heavy" style={{ margin: 0 }}>My gems</h1>
        <EmptyState
          title="No gems saved yet"
          hint="Save a gem from Discover and it shows up here with your notes."
          ctaLabel="Find a gem"
          ctaHref="/discover"
        />
      </main>
    );
  }

  return (
    <main style={page}>
      <header style={{ marginBottom: 18 }}>
        <h1 className="t-2xl w-heavy" style={{ margin: 0 }}>My gems</h1>
        <p className="t-sm" style={{ color: "var(--muted)", marginTop: 6 }}>{saved.length} saved.</p>
      </header>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "center", justifyContent: "space-between", marginBottom: 22 }}>
        <ViewSwitch view={view} onChange={setView} />
        <div style={{ display: "flex", gap: 14, alignItems: "center" }}>
          {selected.length > 0 && (
            <span className="eyebrow">{selected.length} selected{view !== "compare" ? " — open Compare" : ""}</span>
          )}
          {tags.length > 0 && view !== "compare" && (
            <label className="eyebrow" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
              Tag
              <select value={tag} onChange={(e) => setTag(e.target.value)} style={selectStyle}>
                <option value="">All</option>
                {tags.map((t) => (
                  <option key={t} value={t}>#{t}</option>
                ))}
              </select>
            </label>
          )}
        </div>
      </div>

      {view === "list" && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(230px, 1fr))", gap: 12 }}>
          {filtered.map((g) => (
            <SavedCard key={g.advertiserId} gem={g} selected={selected.includes(g.advertiserId)} onToggleSelect={() => toggleSelect(g.advertiserId)} />
          ))}
        </div>
      )}

      {view === "board" && <StageBoard gems={filtered} />}

      {view === "compare" &&
        (compareGems.length >= 2 ? (
          <CompareView gems={compareGems} />
        ) : (
          <EmptyState
            title="Pick 2 gems to compare"
            hint="In the list view, tick the box on 2 to 5 gems, then come back here."
            ctaLabel="Back to list"
            onCta={() => setView("list")}
          />
        ))}
    </main>
  );
}
