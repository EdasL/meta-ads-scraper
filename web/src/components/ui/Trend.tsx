"use client";

export type TrendTone = "up" | "down" | "flat";

const TONE_COLOR: Record<TrendTone, string> = {
  up: "var(--up)",
  down: "var(--down)",
  flat: "var(--ink-soft)",
};

/** Trend glyph (↗ → ↘) + label. Caller supplies the glyph, text, and tone. */
export function Trend({ glyph, label, tone = "flat" }: { glyph: string; label: string; tone?: TrendTone }) {
  const color = TONE_COLOR[tone];
  return (
    <span
      className="font-mono nums"
      style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: "var(--fs-sm)", color }}
    >
      <span aria-hidden style={{ fontSize: "var(--fs-md)", lineHeight: 1 }}>{glyph}</span>
      {label}
    </span>
  );
}
