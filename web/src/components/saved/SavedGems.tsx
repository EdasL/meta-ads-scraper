"use client";

import { useMemo, useState } from "react";
import { useSavedList } from "@/lib/useSaved";
import { EmptyState } from "@/components/ui";
import { GroupBoard } from "./GroupBoard";

const page: React.CSSProperties = { maxWidth: 1100, margin: "0 auto", padding: "24px 22px 80px" };
const selectStyle: React.CSSProperties = {
  background: "var(--surface-3)",
  border: "1px solid var(--line)",
  color: "var(--ink)",
  fontFamily: "var(--font-mono), monospace",
  fontSize: "var(--fs-xs)",
  padding: "5px 8px",
};

export function SavedGems() {
  const saved = useSavedList();
  const [tag, setTag] = useState("");

  const tags = useMemo(() => Array.from(new Set(saved.flatMap((g) => g.tags))).sort(), [saved]);
  const filtered = useMemo(() => (tag ? saved.filter((g) => g.tags.includes(tag)) : saved), [saved, tag]);

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

      {tags.length > 0 && (
        <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 22 }}>
          <label className="eyebrow" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            Tag
            <select value={tag} onChange={(e) => setTag(e.target.value)} style={selectStyle}>
              <option value="">All</option>
              {tags.map((t) => (
                <option key={t} value={t}>#{t}</option>
              ))}
            </select>
          </label>
        </div>
      )}

      <GroupBoard gems={filtered} />
    </main>
  );
}
