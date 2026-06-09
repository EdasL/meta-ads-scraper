"use client";

import { scoreBand } from "@/lib/why";

/**
 * GemScore as the hero number. `card` = inline number + band word; `detail` =
 * bordered box with /100 + band. The band word ("rare gem" etc.) replaces raw
 * cutoffs so the user reads meaning, not a threshold.
 */
export function ScoreBadge({ score, variant = "card" }: { score: number; variant?: "card" | "detail" }) {
  const band = scoreBand(score);
  const n = Math.round(score);

  if (variant === "detail") {
    return (
      <div
        style={{
          display: "inline-flex",
          flexDirection: "column",
          alignItems: "center",
          border: "1.5px solid var(--ink)",
          background: "var(--paper)",
          boxShadow: "var(--shadow-hard)",
          padding: "10px 18px",
          minWidth: 92,
        }}
      >
        <span className="t-2xl w-heavy nums" style={{ lineHeight: 1 }}>{n}</span>
        <span className="eyebrow" style={{ marginTop: 5 }}>/ 100 · {band}</span>
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", lineHeight: 1 }}>
      <span className="t-xl w-heavy nums">{n}</span>
      <span className="eyebrow" style={{ marginTop: 2 }}>{band}</span>
    </div>
  );
}
