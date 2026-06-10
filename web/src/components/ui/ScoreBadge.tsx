"use client";

/**
 * GemScore badge — the real 0-100 score from the matview, nothing derived.
 * `card` = number + "GemScore" caption; `detail` = bordered box with /100;
 * `corner` = small overlay chip for the hero-led card.
 *
 * The number is color-stepped so a column of scores scans without reading:
 * 70+ strong (green), 50-69 middling (amber), below 50 plain ink.
 */
function scoreColor(n: number, onOverlay = false): string {
  // The corner chip sits on photos behind a fixed dark scrim, so it needs
  // fixed light hexes, not theme vars.
  if (onOverlay) return n >= 70 ? "#4ade80" : n >= 50 ? "#fbbf24" : "#ededed";
  return n >= 70 ? "var(--up)" : n >= 50 ? "var(--warn)" : "var(--ink)";
}

export function ScoreBadge({ score, variant = "card" }: { score: number; variant?: "card" | "detail" | "corner" }) {
  const n = Math.round(score);

  if (variant === "corner") {
    return (
      <div
        title={`GemScore ${n} / 100`}
        style={{
          display: "inline-flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          minWidth: 40,
          padding: "4px 7px",
          // Fixed dark scrim — this chip overlays imagery in both themes.
          border: "1.5px solid #ededed",
          background: "rgba(13, 13, 15, 0.82)",
          backdropFilter: "blur(2px)",
          lineHeight: 1,
        }}
      >
        <span className="t-md w-heavy nums" style={{ color: scoreColor(n, true) }}>{n}</span>
        <span className="font-mono" style={{ fontSize: "0.55rem", letterSpacing: "0.08em", color: "#a3a3a3", marginTop: 2 }}>/100</span>
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
        <span className="t-2xl w-heavy nums" style={{ lineHeight: 1, color: scoreColor(n) }}>{n}</span>
        <span className="eyebrow" style={{ marginTop: 5 }}>/ 100 GemScore</span>
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", lineHeight: 1 }}>
      <span className="t-xl w-heavy nums" style={{ color: scoreColor(n) }}>{n}</span>
      <span className="eyebrow" style={{ marginTop: 2 }}>GemScore</span>
    </div>
  );
}
