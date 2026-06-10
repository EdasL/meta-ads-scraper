"use client";

import { useState } from "react";
import { IconChevronDown, IconChevronRight } from "@tabler/icons-react";
import { GEMSCORE_GLOSSARY, type Trend as TrendData } from "@/lib/why";
import { InfoDot, ScoreBadge, Trend } from "@/components/ui";
import { markExpandedWhy } from "@/lib/onboarding";
import type { GemDossier } from "@/types/contracts";

function ExplainerField({ label, value }: { label: string; value: string }) {
  if (!value) return null;
  return (
    <div>
      <div className="eyebrow" style={{ color: "var(--ink)", marginBottom: 4 }}>{label}</div>
      <p className="t-sm" style={{ margin: 0, color: "var(--ink-soft)" }}>{value}</p>
    </div>
  );
}

/**
 * The verdict, top of the dossier: the real GemScore, the real ad-count trend
 * computed from the weekly scaling series, and — when the LLM explainer has
 * been generated — a "Why this might be working" disclosure with its three
 * fields, clearly labeled as generated. No derived status words.
 */
export function GemVerdict({ gem, trend }: { gem: GemDossier; trend: TrendData }) {
  const [open, setOpen] = useState(false);
  const { explainer } = gem;

  const glyph = trend.pct == null ? null : trend.dir === "up" ? "↗" : trend.dir === "down" ? "↘" : "→";
  const tone = trend.pct == null ? ("flat" as const) : trend.dir;
  let trendLabel: string;
  if (trend.pct == null) trendLabel = "no trend data yet";
  else if (trend.dir === "up") trendLabel = `Up ${trend.pct}% · ${trend.multiple}x`;
  else if (trend.dir === "down") trendLabel = `Down ${Math.abs(trend.pct)}% · ${trend.multiple}x`;
  else trendLabel = `Flat · ${trend.multiple}x`;

  return (
    <section style={{ border: "1.5px solid var(--ink)", background: "var(--surface-1)", padding: "20px 22px", boxShadow: "var(--shadow-hard)" }}>
      <div style={{ display: "flex", gap: 20, alignItems: "flex-start", flexWrap: "wrap" }}>
        <ScoreBadge score={gem.gem_score} variant="detail" />
        <div style={{ flex: "1 1 320px", minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            <Trend glyph={glyph} label={trendLabel} tone={tone} />
            <InfoDot text={GEMSCORE_GLOSSARY} label="what GemScore means" />
          </div>

          {explainer && (
            <div style={{ marginTop: 14 }}>
              <button
                type="button"
                onClick={() => {
                  setOpen((o) => {
                    if (!o) markExpandedWhy();
                    return !o;
                  });
                }}
                aria-expanded={open}
                className="font-mono caps"
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 5,
                  background: "transparent",
                  border: "none",
                  cursor: "pointer",
                  color: "var(--muted)",
                  fontSize: "var(--fs-xs)",
                  letterSpacing: "0.08em",
                  padding: 0,
                }}
              >
                {open ? <IconChevronDown size={14} /> : <IconChevronRight size={14} />}
                Why this might be working
              </button>

              {open && (
                <div style={{ marginTop: 12, paddingTop: 12, borderTop: "1px solid var(--line)", display: "flex", flexDirection: "column", gap: 12 }}>
                  <ExplainerField label="What it does" value={explainer.what_it_does} />
                  <ExplainerField label="Problem it solves" value={explainer.problem_solved} />
                  <ExplainerField label="Why the ads suggest traction" value={explainer.why_working} />
                  <p className="font-mono" style={{ margin: 0, fontSize: "var(--fs-xs)", color: "var(--muted)" }}>
                    Generated from this app&apos;s ads and store listing.
                  </p>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
