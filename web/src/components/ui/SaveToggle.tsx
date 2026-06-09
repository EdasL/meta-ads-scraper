"use client";

import { IconStar, IconStarFilled } from "@tabler/icons-react";
import type { ShortlistRow } from "@/types/contracts";
import { toggleSave, useIsSaved } from "@/lib/useSaved";

/**
 * One-keystroke save. Zero decisions: toggles the gem into the default
 * watchlist (freezing its score snapshot). Inverted when saved.
 */
export function SaveToggle({
  row,
  size = "md",
}: {
  row: ShortlistRow;
  size?: "sm" | "md";
}) {
  const saved = useIsSaved(row.advertiser_id);
  const small = size === "sm";
  return (
    <button
      type="button"
      aria-pressed={saved}
      aria-label={saved ? "stop watching" : "watch this gem"}
      onClick={(e) => {
        e.stopPropagation();
        e.preventDefault();
        toggleSave(row);
      }}
      className="font-mono caps"
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        cursor: "pointer",
        padding: small ? "4px 9px" : "7px 13px",
        fontSize: "var(--fs-xs)",
        letterSpacing: "0.1em",
        border: "1.5px solid var(--ink)",
        background: saved ? "var(--ink)" : "transparent",
        color: saved ? "var(--bg)" : "var(--ink)",
        whiteSpace: "nowrap",
      }}
    >
      {saved ? <IconStarFilled size={14} /> : <IconStar size={14} stroke={1.75} />}
      {saved ? "saved" : "watch"}
    </button>
  );
}
