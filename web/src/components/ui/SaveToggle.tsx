"use client";

import { IconHeart, IconHeartFilled } from "@tabler/icons-react";
import type { ShortlistRow } from "@/types/contracts";
import { toggleSave, useIsSaved } from "@/lib/useSaved";

/**
 * One-keystroke save. Zero decisions: toggles the gem into the default
 * watchlist (freezing its score snapshot). Icon-only heart to keep it
 * compact; inverted when saved.
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
      aria-label={saved ? "Remove from saved" : "Save this gem"}
      title={saved ? "Remove from saved" : "Save this gem"}
      onClick={(e) => {
        e.stopPropagation();
        e.preventDefault();
        toggleSave(row);
      }}
      style={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        cursor: "pointer",
        padding: small ? 4 : 6,
        border: "1.5px solid var(--ink)",
        background: saved ? "var(--ink)" : "transparent",
        color: saved ? "var(--bg)" : "var(--ink)",
      }}
    >
      {saved ? <IconHeartFilled size={small ? 14 : 16} /> : <IconHeart size={small ? 14 : 16} stroke={1.75} />}
    </button>
  );
}
