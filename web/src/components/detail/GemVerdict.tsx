"use client";

import { becauseLine, GEMSCORE_GLOSSARY, statusChip, type Trend as TrendData } from "@/lib/why";
import { InfoDot, ScoreBadge, StatusBadge, Trend } from "@/components/ui";
import { GrugExplainer } from "@/components/GrugExplainer";
import { markExpandedWhy } from "@/lib/onboarding";
import type { GemDossier } from "@/types/contracts";

/**
 * The verdict, top of the dossier. Plain voice: big score + band + status +
 * real trend + one clear reason. The deeper, grug-voiced breakdown lives in the
 * GrugExplainer disclosure (the one place grug speaks) — it carries the three
 * explainer fields + the quantified "why it's a gem".
 */
export function GemVerdict({ gem, trend }: { gem: GemDossier; trend: TrendData }) {
  const status = statusChip(gem);
  const glyph = trend.dir === "up" ? "↗" : trend.dir === "down" ? "↘" : "→";
  const tone = trend.dir;
  let trendLabel: string;
  if (trend.pct == null) trendLabel = "New — watch it";
  else if (trend.dir === "up") trendLabel = `Up ${trend.pct}% · ${trend.multiple}x`;
  else if (trend.dir === "down") trendLabel = `Down ${Math.abs(trend.pct)}% · ${trend.multiple}x`;
  else trendLabel = `Flat · ${trend.multiple}x`;

  const distinctCreatives = gem.scaling.reduce((m, p) => Math.max(m, p.distinct_creative_count), 0);

  return (
    <section style={{ border: "1.5px solid var(--ink)", background: "var(--surface-1)", padding: "20px 22px", boxShadow: "var(--shadow-hard)" }}>
      <div style={{ display: "flex", gap: 20, alignItems: "flex-start", flexWrap: "wrap" }}>
        <ScoreBadge score={gem.gem_score} variant="detail" />
        <div style={{ flex: "1 1 320px", minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            <StatusBadge status={status} />
            <Trend glyph={glyph} label={trendLabel} tone={tone} />
            <InfoDot text={GEMSCORE_GLOSSARY} label="what GemScore means" />
          </div>
          <p className="t-md" style={{ marginTop: 10 }}>{becauseLine(gem)}</p>
          <div style={{ marginTop: 12 }}>
            <GrugExplainer row={gem} distinctCreatives={distinctCreatives} onOpen={markExpandedWhy} />
          </div>
        </div>
      </div>
    </section>
  );
}
