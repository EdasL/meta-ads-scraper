"use client";

import { INK, PAPER } from "./palette";

/**
 * A clean status tag (was a distressed rubber stamp). Two B&W treatments:
 * "ink" = outlined, "red" = inverted (solid black). Monochrome, sharp.
 */
export function Stamp({
  label,
  tone = "ink",
  className,
}: {
  label: string;
  tone?: "ink" | "red";
  rotate?: number;
  className?: string;
  seedKey?: string;
}) {
  const inverted = tone === "red";
  return (
    <span
      className={`font-mono ${className ?? ""}`}
      style={{
        display: "inline-block",
        padding: "4px 10px",
        border: `1.5px solid ${INK}`,
        background: inverted ? INK : "transparent",
        color: inverted ? PAPER : INK,
        fontSize: "0.64rem",
        fontWeight: 600,
        letterSpacing: "0.12em",
        textTransform: "uppercase",
        whiteSpace: "nowrap",
      }}
    >
      {label}
    </span>
  );
}
