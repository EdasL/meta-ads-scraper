"use client";

import { useState } from "react";
import { IconChevronDown, IconChevronRight } from "@tabler/icons-react";
import { becauseLine, GEMSCORE_GLOSSARY, pushPull, statusChip, type Trend as TrendData } from "@/lib/why";
import { markExpandedWhy } from "@/lib/onboarding";
import { InfoDot, ScoreBadge, SignalChips, StatusBadge, Trend } from "@/components/ui";
import type { GemDossier } from "@/types/contracts";

/**
 * The verdict, top of the dossier. Altitude 1: big score + band + status +
 * real trend + one grug reason. Altitude 2 (behind "how grug know?"): the
 * push/pull breakdown. The math stays hidden until asked for (spec §5.2).
 */
export function GemVerdict({ gem, trend }: { gem: GemDossier; trend: TrendData }) {
  const [open, setOpen] = useState(false);
  const status = statusChip(gem);
  const { push, pull } = pushPull(gem);
  const glyph = trend.dir === "up" ? "↗" : trend.dir === "down" ? "↘" : "→";
  let trendLabel: string;
  if (trend.pct == null) trendLabel = "new — watch it";
  else if (trend.dir === "up") trendLabel = `up ${trend.pct}% · ${trend.multiple}x`;
  else if (trend.dir === "down") trendLabel = `down ${Math.abs(trend.pct)}% · ${trend.multiple}x`;
  else trendLabel = `flat · ${trend.multiple}x`;

  return (
    <section style={{ border: "1.5px solid var(--ink)", background: "var(--surface-1)", padding: "20px 22px", boxShadow: "var(--shadow-hard)" }}>
      <div style={{ display: "flex", gap: 20, alignItems: "flex-start", flexWrap: "wrap" }}>
        <ScoreBadge score={gem.gem_score} variant="detail" />
        <div style={{ flex: "1 1 320px", minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            <StatusBadge status={status} />
            <Trend glyph={glyph} label={trendLabel} />
            <InfoDot text={GEMSCORE_GLOSSARY} label="what gemscore mean" />
          </div>
          <p className="t-md" style={{ marginTop: 10 }}>{becauseLine(gem)}</p>
          <button
            type="button"
            onClick={() => {
              setOpen((o) => !o);
              markExpandedWhy(); // activation: "see why it climb"
            }}
            aria-expanded={open}
            className="font-mono caps"
            style={{ display: "inline-flex", alignItems: "center", gap: 5, marginTop: 12, background: "transparent", border: "none", cursor: "pointer", color: "var(--muted)", fontSize: "var(--fs-xs)", letterSpacing: "0.08em", padding: 0 }}
          >
            {open ? <IconChevronDown size={14} /> : <IconChevronRight size={14} />} how grug know?
          </button>
        </div>
      </div>

      {open && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: 24, marginTop: 18, paddingTop: 18, borderTop: "1px solid var(--line)" }}>
          <div>
            <h3 className="eyebrow" style={{ margin: "0 0 10px", color: "var(--ink)" }}>why it climb</h3>
            {push.length ? <SignalChips chips={push} /> : <p className="t-sm" style={{ color: "var(--muted)" }}>not much push yet.</p>}
          </div>
          <div>
            <h3 className="eyebrow" style={{ margin: "0 0 10px", color: "var(--ink)" }}>what hold it back</h3>
            {pull.length ? <SignalChips chips={pull} /> : <p className="t-sm" style={{ color: "var(--muted)" }}>nothing hold it back. rare.</p>}
          </div>
        </div>
      )}
    </section>
  );
}
