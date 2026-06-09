"use client";

import { useMemo, useState } from "react";
import { IconChevronDown, IconChevronRight, IconFileText } from "@tabler/icons-react";
import type { ShortlistRow } from "@/types/contracts";

function slug(g: ShortlistRow): string {
  const base = (g.app_name ?? g.page_name).toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "");
  return `${base || "gem"}.gem`;
}

/**
 * DAW-browser-style file tree (NRTHVIEW vibe): cohorts as folders, gems as
 * .gem files. Clicking a folder filters the board; clicking a file opens it.
 */
export function SessionTree({
  rows,
  activeCohort,
  onPickCohort,
  onOpenGem,
}: {
  rows: ShortlistRow[];
  activeCohort: string;
  onPickCohort: (cohort: string) => void;
  onOpenGem: (id: number) => void;
}) {
  const groups = useMemo(() => {
    const m = new Map<string, ShortlistRow[]>();
    for (const r of rows) {
      const arr = m.get(r.cohort) ?? [];
      arr.push(r);
      m.set(r.cohort, arr);
    }
    return Array.from(m.entries());
  }, [rows]);

  const [open, setOpen] = useState<Record<string, boolean>>({});
  const isOpen = (c: string) => open[c] ?? true;

  const row: React.CSSProperties = {
    display: "block",
    width: "100%",
    textAlign: "left",
    background: "transparent",
    border: "none",
    cursor: "pointer",
    fontFamily: "var(--font-mono), monospace",
    fontSize: "0.7rem",
    color: "var(--ink)",
    padding: "3px 0",
  };

  return (
    <aside
      style={{
        width: 232,
        flexShrink: 0,
        borderRight: "1.5px solid var(--ink)",
        background: "var(--paper)",
        overflowY: "auto",
        padding: "14px 14px 40px",
      }}
    >
      <div className="hud" style={{ color: "var(--ink)", marginBottom: 4, display: "flex", alignItems: "center", gap: 4 }}>
        <IconChevronDown size={13} stroke={2} /> gems
      </div>
      <button
        style={{ ...row, color: activeCohort === "" ? "var(--ink)" : "var(--muted)", paddingLeft: 12, fontWeight: activeCohort === "" ? 700 : 400 }}
        onClick={() => onPickCohort("")}
      >
        all_cohorts/
      </button>

      {groups.map(([cohort, gems]) => (
        <div key={cohort} style={{ marginTop: 6 }}>
          <button
            style={{ ...row, paddingLeft: 12, fontWeight: activeCohort === cohort ? 700 : 400, display: "flex", alignItems: "center", gap: 4 }}
            onClick={() => {
              setOpen((o) => ({ ...o, [cohort]: !isOpen(cohort) }));
              onPickCohort(activeCohort === cohort ? "" : cohort);
            }}
          >
            {isOpen(cohort) ? <IconChevronDown size={13} stroke={2} /> : <IconChevronRight size={13} stroke={2} />}
            {cohort.toLowerCase().replace(/[^a-z0-9]+/g, "_")}/
          </button>
          {isOpen(cohort) &&
            gems
              .sort((a, b) => b.gem_score - a.gem_score)
              .map((g) => (
                <button
                  key={g.advertiser_id}
                  style={{ ...row, paddingLeft: 30, display: "flex", justifyContent: "space-between", gap: 8 }}
                  onClick={() => onOpenGem(g.advertiser_id)}
                  title="open file"
                >
                  <span style={{ display: "flex", alignItems: "center", gap: 5, overflow: "hidden" }}>
                    <IconFileText size={12} stroke={1.75} style={{ flexShrink: 0, color: "var(--muted)" }} />
                    <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{slug(g)}</span>
                  </span>
                  <span style={{ color: "var(--muted)" }}>{Math.round(g.gem_score)}</span>
                </button>
              ))}
        </div>
      ))}
    </aside>
  );
}
