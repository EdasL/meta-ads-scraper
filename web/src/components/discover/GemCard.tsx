"use client";

import Link from "next/link";
import type { ShortlistRow } from "@/types/contracts";
import { growthLine } from "@/lib/why";
import { AppIcon, SaveToggle, Trend } from "@/components/ui";

/**
 * One gem in the Discover feed — minimal by design: app logo, name, and the
 * observed ad-growth line, all straight from the ads/DB row. The whole card
 * links to the dossier; the save button opts out of nav.
 */
export function GemCard({ row }: { row: ShortlistRow }) {
  const g = growthLine(row.growth, row);
  const name = row.app_name ?? row.page_name;

  return (
    <Link
      href={`/gems/${row.advertiser_id}`}
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 8,
        textDecoration: "none",
        color: "var(--ink)",
        background: "var(--paper)",
        border: "1.5px solid var(--ink)",
        boxShadow: "var(--shadow-hard)",
        padding: 10,
      }}
    >
      <div style={{ display: "flex", alignItems: "flex-start", gap: 10 }}>
        <AppIcon src={row.icon_url} name={name} size={44} />
        <div
          className="t-sm w-strong"
          style={{
            flex: 1,
            minWidth: 0,
            display: "-webkit-box",
            WebkitLineClamp: 2,
            WebkitBoxOrient: "vertical",
            overflow: "hidden",
            wordBreak: "break-word",
            // Always reserve two lines so the growth row sits at the same
            // height on every card.
            minHeight: "calc(2 * var(--lh-sm))",
          }}
        >
          {name}
        </div>
        <SaveToggle row={row} size="sm" />
      </div>

      <div>
        <div className="eyebrow" style={{ marginBottom: 2 }}>{g.kind === "growth" ? "ad growth" : "ads live"}</div>
        <Trend glyph={g.glyph} label={g.text} tone={g.tone} />
      </div>
    </Link>
  );
}
