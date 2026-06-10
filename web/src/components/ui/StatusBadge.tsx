"use client";

import type { StatusChip } from "@/lib/why";

/**
 * One-word momentum chip, color-coded by status. "New" / "Hot" carry a tinted
 * fill; "Steady" / "Cooling" are quiet outlines. Plain display labels (keys are
 * kept internal). Color borrows the semantic status hues.
 */
const STATUS: Record<StatusChip, { label: string; color: string; fill: boolean }> = {
  "new spark": { label: "New", color: "var(--status-spark)", fill: true },
  "hot now": { label: "Hot", color: "var(--status-hot)", fill: true },
  steady: { label: "Steady", color: "var(--status-steady)", fill: false },
  cooling: { label: "Cooling", color: "var(--status-cool)", fill: false },
};

export function StatusBadge({ status }: { status: StatusChip }) {
  const { label, color, fill } = STATUS[status];
  return (
    <span
      className="font-mono caps"
      style={{
        display: "inline-block",
        padding: "3px 8px",
        fontSize: "var(--fs-xs)",
        letterSpacing: "0.1em",
        border: `1px solid ${color}`,
        background: fill ? color : "transparent",
        color: fill ? "var(--bg)" : color,
        whiteSpace: "nowrap",
      }}
    >
      {label}
    </span>
  );
}
