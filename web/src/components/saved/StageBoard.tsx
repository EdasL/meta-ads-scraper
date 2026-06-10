"use client";

import Link from "next/link";
import { IconChevronLeft, IconChevronRight } from "@tabler/icons-react";
import { STAGES, type SavedGem, type Stage } from "@/lib/saved";
import { setStage } from "@/lib/useSaved";

const moveBtn: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  background: "transparent",
  border: "1px solid var(--line)",
  color: "var(--ink)",
  cursor: "pointer",
  padding: "2px 6px",
  lineHeight: 0,
};

/** Kanban over the saved gems: just saw → watching → looks real → building.
 * Move with ◂ ▸ (keyboard-friendly, robust). */
export function StageBoard({ gems }: { gems: SavedGem[] }) {
  const order: Stage[] = STAGES.map((s) => s.id);

  const move = (g: SavedGem, dir: -1 | 1) => {
    const i = order.indexOf(g.stage);
    const ni = Math.min(Math.max(i + dir, 0), order.length - 1);
    if (ni !== i) setStage(g.advertiserId, order[ni]);
  };

  return (
    <div style={{ display: "grid", gridTemplateColumns: `repeat(${STAGES.length}, minmax(170px, 1fr))`, gap: 14, overflowX: "auto" }}>
      {STAGES.map((col) => {
        const items = gems.filter((g) => g.stage === col.id);
        return (
          <div key={col.id} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <div className="eyebrow" style={{ display: "flex", justifyContent: "space-between", borderBottom: "1px solid var(--line)", paddingBottom: 6 }}>
              <span style={{ color: "var(--ink)" }}>{col.label}</span>
              <span className="nums">{items.length}</span>
            </div>
            {items.map((g) => (
              <div key={g.advertiserId} style={{ border: "1.5px solid var(--ink)", background: "var(--paper)", padding: 10, display: "flex", flexDirection: "column", gap: 8 }}>
                <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "baseline" }}>
                  <Link href={`/gems/${g.advertiserId}`} className="t-sm w-strong" style={{ color: "var(--ink)", textDecoration: "none" }}>
                    {g.appName ?? g.pageName}
                  </Link>
                  <span className="t-md w-heavy nums">{Math.round(g.scoreSnapshot.gemScore)}</span>
                </div>
                <div style={{ display: "flex", justifyContent: "space-between" }}>
                  <button type="button" onClick={() => move(g, -1)} aria-label="move back a stage" disabled={col.id === order[0]} style={{ ...moveBtn, opacity: col.id === order[0] ? 0.3 : 1 }}>
                    <IconChevronLeft size={14} />
                  </button>
                  <button type="button" onClick={() => move(g, 1)} aria-label="move on a stage" disabled={col.id === order[order.length - 1]} style={{ ...moveBtn, opacity: col.id === order[order.length - 1] ? 0.3 : 1 }}>
                    <IconChevronRight size={14} />
                  </button>
                </div>
              </div>
            ))}
            {items.length === 0 && <div className="t-xs" style={{ color: "var(--muted)" }}>—</div>}
          </div>
        );
      })}
    </div>
  );
}
