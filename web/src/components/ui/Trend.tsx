"use client";

/** Trend glyph (↗ → ↘) + label. Caller supplies the glyph and the text. */
export function Trend({ glyph, label }: { glyph: string; label: string }) {
  return (
    <span
      className="font-mono nums"
      style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: "var(--fs-sm)", color: "var(--ink-soft)" }}
    >
      <span aria-hidden style={{ fontSize: "var(--fs-md)", lineHeight: 1 }}>{glyph}</span>
      {label}
    </span>
  );
}
