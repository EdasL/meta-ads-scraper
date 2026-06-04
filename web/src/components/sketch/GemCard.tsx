"use client";

import type { ShortlistRow } from "@/types/contracts";
import { Stamp } from "./Stamp";
import { INK, PAPER } from "./palette";

function statusOf(g: ShortlistRow): { label: string; tone: "ink" | "red" } {
  if (g.alert) return { label: "getting big", tone: "red" };
  if (g.max_longevity_days >= 14) return { label: "real · 14 day+", tone: "ink" };
  return { label: "still hidden", tone: "ink" };
}

function Axis({ name, value }: { name: string; value: number }) {
  const pct = Math.round(value * 100);
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      <span className="font-mono" style={{ fontSize: "0.58rem", width: 20, color: "var(--muted)" }}>{name}</span>
      <div style={{ flex: 1, height: 4, background: "var(--paper-2)", position: "relative" }}>
        <div style={{ position: "absolute", inset: 0, width: `${pct}%`, background: INK }} />
      </div>
      <span className="font-mono" style={{ fontSize: "0.58rem", width: 22, textAlign: "right" }}>{pct}</span>
    </div>
  );
}

export function GemCard({
  gem,
  width = 256,
  onOpen,
}: {
  gem: ShortlistRow;
  width?: number;
  onOpen?: () => void;
}) {
  const status = statusOf(gem);
  const title = gem.app_name ?? gem.page_name;

  return (
    <div
      onClick={onOpen}
      style={{
        width,
        background: PAPER,
        border: `1.5px solid ${INK}`,
        cursor: onOpen ? "pointer" : "default",
        boxShadow: "5px 5px 0 var(--ink)",
        userSelect: "none",
      }}
    >
      {/* header strip */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          padding: "6px 12px",
          borderBottom: `1.5px solid ${INK}`,
        }}
      >
        <span className="eyebrow" style={{ color: INK }}>{gem.cohort}</span>
      </div>

      <div style={{ padding: "14px 14px 16px" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 10 }}>
          <div className="display" style={{ fontSize: "1.55rem", textTransform: "uppercase" }}>{title}</div>
          <div style={{ textAlign: "right", flexShrink: 0 }}>
            <div className="display-x" style={{ fontSize: "2.6rem" }}>{Math.round(gem.gem_score)}</div>
            <div className="eyebrow">Gemscore</div>
          </div>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 5, marginTop: 14 }}>
          <Axis name="SV" value={gem.scaling_velocity} />
          <Axis name="UR" value={gem.under_radar} />
          <Axis name="FI" value={gem.funnel_intent} />
        </div>

        <div
          className="font-mono"
          style={{ display: "flex", justifyContent: "space-between", fontSize: "0.62rem", marginTop: 14, color: "var(--ink-soft)" }}
        >
          <span>{gem.active_ad_count} ads now</span>
          <span>{gem.max_longevity_days}d up</span>
        </div>

        <div style={{ marginTop: 12 }}>
          <Stamp label={status.label} tone={status.tone} />
        </div>
      </div>
    </div>
  );
}
