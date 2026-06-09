"use client";

import type { SignalChip, SignalTier } from "@/lib/why";

/**
 * The three GemScore signals. `compact` (card) = label + filled/hollow dot.
 * Full (detail) = a 3-segment strength bar + a "big push / some push / small"
 * caption, so the user sees what lifts vs holds back the score.
 */
function segments(tier: SignalTier): number {
  return tier === "big" ? 3 : tier === "some" ? 2 : 1;
}

function Dot({ filled }: { filled: boolean }) {
  return (
    <span
      aria-hidden
      style={{ width: 8, height: 8, display: "inline-block", border: "1px solid var(--ink)", background: filled ? "var(--ink)" : "transparent" }}
    />
  );
}

function Strength({ tier }: { tier: SignalTier }) {
  const n = segments(tier);
  return (
    <span aria-hidden style={{ display: "inline-flex", gap: 3 }}>
      {[0, 1, 2].map((i) => (
        <span key={i} style={{ width: 11, height: 15, border: "1px solid var(--ink)", background: i < n ? "var(--ink)" : "transparent" }} />
      ))}
    </span>
  );
}

const TIER_WORD: Record<SignalTier, string> = { big: "big push", some: "some push", small: "small" };

export function SignalChips({ chips, compact = false }: { chips: SignalChip[]; compact?: boolean }) {
  if (compact) {
    return (
      <div style={{ display: "flex", flexWrap: "wrap", gap: "6px 12px" }}>
        {chips.map((c) => (
          <span
            key={c.key}
            className="font-mono"
            style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: "var(--fs-xs)", color: c.filled ? "var(--ink)" : "var(--muted)" }}
          >
            <Dot filled={c.filled} />
            {c.label}
          </span>
        ))}
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {chips.map((c) => (
        <div key={c.key} style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <Strength tier={c.tier} />
          <span className="w-ui t-sm" style={{ color: c.filled ? "var(--ink)" : "var(--muted)" }}>{c.label}</span>
          <span className="eyebrow" style={{ marginLeft: "auto" }}>{TIER_WORD[c.tier]}</span>
        </div>
      ))}
    </div>
  );
}
