"use client";

import Link from "next/link";
import { STAGES, type SavedGem } from "@/lib/saved";

const cellHead: React.CSSProperties = { padding: "8px 10px", borderBottom: "1.5px solid var(--ink)", textAlign: "left", verticalAlign: "bottom" };
const cellLabel: React.CSSProperties = { padding: "8px 10px", borderBottom: "1px solid var(--line)", whiteSpace: "nowrap", color: "var(--ink)" };
const cell: React.CSSProperties = { padding: "8px 10px", borderBottom: "1px solid var(--line)", color: "var(--ink-soft)" };

type Row = { label: string; get: (g: SavedGem) => string };

const pct = (v: number) => `${Math.round(v * 100)}`;

const ROWS: Row[] = [
  { label: "GemScore", get: (g) => `${Math.round(g.scoreSnapshot.gemScore)}` },
  { label: "Scaling", get: (g) => pct(g.scoreSnapshot.scalingVelocity) },
  { label: "Under radar", get: (g) => pct(g.scoreSnapshot.underRadar) },
  { label: "Funnel", get: (g) => pct(g.scoreSnapshot.funnelIntent) },
  { label: "Ads at save", get: (g) => `${g.scoreSnapshot.activeAdCount}` },
  { label: "Kind", get: (g) => g.cohort },
  { label: "Stage", get: (g) => STAGES.find((s) => s.id === g.stage)?.label ?? g.stage.replace(/_/g, " ") },
];

/** Line up 2-5 saved gems side by side on the snapshot signals. Does the
 * "which do I build?" comparison for the founder (spec §5.3). */
export function CompareView({ gems }: { gems: SavedGem[] }) {
  // Tag the single strongest gem per numeric signal as a quiet highlight.
  const bestBy = (key: "scaling_velocity" | "under_radar" | "funnel_intent" | "gemScore"): number | null => {
    if (gems.length < 2) return null;
    let best = 0;
    const val = (g: SavedGem) =>
      key === "gemScore"
        ? g.scoreSnapshot.gemScore
        : key === "scaling_velocity"
          ? g.scoreSnapshot.scalingVelocity
          : key === "under_radar"
            ? g.scoreSnapshot.underRadar
            : g.scoreSnapshot.funnelIntent;
    gems.forEach((g, i) => {
      if (val(g) > val(gems[best])) best = i;
    });
    return best;
  };

  const highlightRow: Record<string, number | null> = {
    GemScore: bestBy("gemScore"),
    Scaling: bestBy("scaling_velocity"),
    "Under radar": bestBy("under_radar"),
    Funnel: bestBy("funnel_intent"),
  };

  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ borderCollapse: "collapse", width: "100%", minWidth: 130 * (gems.length + 1) }}>
        <thead>
          <tr>
            <th style={cellHead} />
            {gems.map((g) => (
              <th key={g.advertiserId} style={cellHead}>
                <Link href={`/gems/${g.advertiserId}`} className="t-sm w-strong" style={{ color: "var(--ink)", textDecoration: "none" }}>
                  {g.appName ?? g.pageName}
                </Link>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {ROWS.map((r) => {
            const hi = highlightRow[r.label];
            return (
              <tr key={r.label}>
                <td className="eyebrow" style={cellLabel}>{r.label}</td>
                {gems.map((g, i) => (
                  <td key={g.advertiserId} className="font-mono nums t-sm" style={{ ...cell, color: hi === i ? "var(--ink)" : "var(--ink-soft)", fontWeight: hi === i ? 700 : 400 }}>
                    {r.get(g)}
                    {hi === i && <span className="eyebrow" style={{ marginLeft: 6, color: "var(--up)" }}>Best</span>}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
