"use client";

import { scoreBand, type ScoreBand } from "@/lib/why";

/**
 * GemScore badge. `card` = inline number + band word; `detail` = bordered box
 * with /100 + band; `corner` = small overlay chip for the hero-led card (score
 * demoted from hero to a corner badge). The band word + a band hue convey
 * meaning without exposing raw cutoffs.
 */
const BAND_COLOR: Record<ScoreBand, string> = {
  weak: "var(--band-weak)",
  maybe: "var(--band-maybe)",
  strong: "var(--band-strong)",
  "rare gem": "var(--band-rare)",
};

export function ScoreBadge({ score, variant = "card" }: { score: number; variant?: "card" | "detail" | "corner" }) {
  const band = scoreBand(score);
  const hue = BAND_COLOR[band];
  const n = Math.round(score);

  if (variant === "corner") {
    return (
      <div
        title={`gemscore ${n} / 100 · ${band}`}
        style={{
          display: "inline-flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          minWidth: 40,
          padding: "4px 7px",
          border: `1.5px solid ${hue}`,
          background: "rgba(13, 13, 15, 0.82)",
          backdropFilter: "blur(2px)",
          lineHeight: 1,
        }}
      >
        <span className="t-md w-heavy nums" style={{ color: hue }}>{n}</span>
        <span className="font-mono" style={{ fontSize: "0.55rem", letterSpacing: "0.08em", color: "var(--muted)", marginTop: 2 }}>/100</span>
      </div>
    );
  }

  if (variant === "detail") {
    return (
      <div
        style={{
          display: "inline-flex",
          flexDirection: "column",
          alignItems: "center",
          border: "1.5px solid var(--ink)",
          background: "var(--paper)",
          boxShadow: "var(--shadow-hard)",
          padding: "10px 18px",
          minWidth: 92,
        }}
      >
        <span className="t-2xl w-heavy nums" style={{ lineHeight: 1, color: hue }}>{n}</span>
        <span className="eyebrow" style={{ marginTop: 5 }}>/ 100 · {band}</span>
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", lineHeight: 1 }}>
      <span className="t-xl w-heavy nums" style={{ color: hue }}>{n}</span>
      <span className="eyebrow" style={{ marginTop: 2 }}>{band}</span>
    </div>
  );
}
