"use client";

import Link from "next/link";
import type { ShortlistRow } from "@/types/contracts";
import { signalChips, statusChip, velocityTrend, whyLine } from "@/lib/why";
import { ScoreBadge, SignalChips, StatusBadge, SaveToggle, Trend } from "@/components/ui";

/**
 * One gem in the Discover feed. Score is the hero; a status chip + qualitative
 * trend say "is it moving?"; one grug line says "why?"; the three signal chips
 * say "how good on each axis?"; and watch saves it in one tap. The whole card
 * links to the dossier (native keyboard focus); the save button opts out of nav.
 */
export function GemCard({ row }: { row: ShortlistRow }) {
  const status = statusChip(row);
  const vt = velocityTrend(row.scaling_velocity);
  const title = row.app_name ?? row.page_name;

  return (
    <Link
      href={`/gems/${row.advertiser_id}`}
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 12,
        textDecoration: "none",
        color: "var(--ink)",
        background: "var(--paper)",
        border: "1.5px solid var(--ink)",
        boxShadow: "var(--shadow-hard)",
        padding: 16,
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 10 }}>
        <ScoreBadge score={row.gem_score} />
        <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 6 }}>
          <StatusBadge status={status} />
          <Trend glyph={vt.glyph} label={vt.word} />
        </div>
      </div>

      <div>
        <div className="t-lg w-heavy caps" style={{ wordBreak: "break-word" }}>{title}</div>
        <div className="eyebrow" style={{ marginTop: 2 }}>
          {row.cohort}
          {row.app_id === null && " · web"}
        </div>
      </div>

      <p className="t-sm" style={{ color: "var(--ink-soft)", margin: 0 }}>{whyLine(row)}</p>

      <SignalChips chips={signalChips(row)} compact />

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: "auto", paddingTop: 4 }}>
        <span className="font-mono nums" style={{ fontSize: "var(--fs-xs)", color: "var(--muted)" }}>
          {row.active_ad_count} ad live · {row.max_longevity_days}d up
        </span>
        <SaveToggle row={row} size="sm" />
      </div>
    </Link>
  );
}
