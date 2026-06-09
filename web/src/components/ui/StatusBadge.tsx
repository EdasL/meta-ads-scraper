"use client";

import type { StatusChip } from "@/lib/why";

/**
 * One-word momentum chip. "new spark" / "hot now" are emphasised (inverted);
 * "steady" is plain outline; "cooling" is muted. Functional, never colored.
 */
const INVERTED: StatusChip[] = ["new spark", "hot now"];

export function StatusBadge({ status }: { status: StatusChip }) {
  const inverted = INVERTED.includes(status);
  const muted = status === "cooling";
  return (
    <span
      className="font-mono caps"
      style={{
        display: "inline-block",
        padding: "3px 8px",
        fontSize: "var(--fs-xs)",
        letterSpacing: "0.1em",
        border: `1px solid ${muted ? "var(--muted)" : "var(--ink)"}`,
        background: inverted ? "var(--ink)" : "transparent",
        color: inverted ? "var(--bg)" : muted ? "var(--muted)" : "var(--ink)",
        whiteSpace: "nowrap",
      }}
    >
      {status}
    </span>
  );
}
