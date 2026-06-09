"use client";

import { IconDiamond, IconRestore } from "@tabler/icons-react";

export function BoardToolbar({
  cohorts,
  cohort,
  minScore,
  count,
  onCohort,
  onMinScore,
  onReset,
}: {
  cohorts: string[];
  cohort: string;
  minScore: number;
  count: number;
  onCohort: (v: string) => void;
  onMinScore: (v: number) => void;
  onReset: () => void;
}) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 22,
        flexWrap: "wrap",
        padding: "12px 22px",
        borderBottom: "1.5px solid var(--ink)",
        background: "var(--paper)",
      }}
    >
      <label className="font-mono" style={{ fontSize: "0.72rem", display: "flex", alignItems: "center", gap: 8 }}>
        cohort
        <select
          value={cohort}
          onChange={(e) => onCohort(e.target.value)}
          className="font-mono"
          style={{ background: "transparent", border: "1.5px solid var(--ink)", padding: "4px 8px", fontSize: "0.72rem" }}
        >
          <option value="">all</option>
          {cohorts.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
      </label>

      <label className="font-mono" style={{ fontSize: "0.72rem", display: "flex", alignItems: "center", gap: 10 }}>
        min gemscore
        <input
          type="range"
          min={0}
          max={100}
          value={minScore}
          onChange={(e) => onMinScore(Number(e.target.value))}
          style={{ accentColor: "var(--ink)" }}
        />
        <span style={{ width: 24 }}>{minScore}</span>
      </label>

      <span className="font-mono" style={{ fontSize: "0.72rem", color: "var(--muted)", display: "inline-flex", alignItems: "center", gap: 6 }}>
        <IconDiamond size={14} stroke={1.75} />
        {count} gem on board
      </span>

      <button
        onClick={onReset}
        className="font-mono"
        style={{
          marginLeft: "auto",
          fontSize: "0.7rem",
          letterSpacing: "0.06em",
          textTransform: "uppercase",
          background: "transparent",
          border: "none",
          cursor: "pointer",
          color: "var(--ink)",
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
        }}
      >
        <IconRestore size={14} stroke={1.75} />
        <span className="uline">put back</span>
      </button>
    </div>
  );
}
