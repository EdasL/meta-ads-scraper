"use client";

import type { SignalChip, SignalKey, SignalTier } from "@/lib/why";

/**
 * The three GemScore signals. `compact` (card) = colored label + filled/hollow
 * dot. Full (detail) = a 3-segment strength bar + a "big push / some push /
 * small" caption. Each signal carries its own hue so the three axes are
 * distinguishable at a glance (color drives scanning, not decoration).
 */
const SIGNAL_COLOR: Record<SignalKey, string> = {
  scaling_velocity: "var(--sig-scaling)",
  under_radar: "var(--sig-radar)",
  funnel_intent: "var(--sig-funnel)",
};

function segments(tier: SignalTier): number {
  return tier === "big" ? 3 : tier === "some" ? 2 : 1;
}

function Dot({ filled, color }: { filled: boolean; color: string }) {
  return (
    <span
      aria-hidden
      style={{ width: 8, height: 8, display: "inline-block", border: `1px solid ${color}`, background: filled ? color : "transparent" }}
    />
  );
}

function Strength({ tier, color }: { tier: SignalTier; color: string }) {
  const n = segments(tier);
  return (
    <span aria-hidden style={{ display: "inline-flex", gap: 3 }}>
      {[0, 1, 2].map((i) => (
        <span key={i} style={{ width: 11, height: 15, border: `1px solid ${color}`, background: i < n ? color : "transparent" }} />
      ))}
    </span>
  );
}

const TIER_WORD: Record<SignalTier, string> = { big: "strong", some: "some", small: "weak" };

export function SignalChips({ chips, compact = false }: { chips: SignalChip[]; compact?: boolean }) {
  if (compact) {
    return (
      <div style={{ display: "flex", flexWrap: "wrap", gap: "6px 12px" }}>
        {chips.map((c) => {
          const color = SIGNAL_COLOR[c.key];
          return (
            <span
              key={c.key}
              className="font-mono"
              style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: "var(--fs-xs)", color: c.filled ? color : "var(--muted)" }}
            >
              <Dot filled={c.filled} color={c.filled ? color : "var(--muted)"} />
              {c.label}
            </span>
          );
        })}
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {chips.map((c) => {
        const color = SIGNAL_COLOR[c.key];
        return (
          <div key={c.key} style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <Strength tier={c.tier} color={c.filled ? color : "var(--muted)"} />
            <span className="w-ui t-sm" style={{ color: c.filled ? "var(--ink)" : "var(--muted)" }}>{c.label}</span>
            <span className="eyebrow" style={{ marginLeft: "auto" }}>{TIER_WORD[c.tier]}</span>
          </div>
        );
      })}
    </div>
  );
}
