"use client";

import Link from "next/link";
import { IconX } from "@tabler/icons-react";
import { STAGES, type SavedGem, type Stage } from "@/lib/saved";
import { setStage, unsave } from "@/lib/useSaved";
import { signalChips } from "@/lib/why";
import { AppIcon, ScoreBadge, SignalChips } from "@/components/ui";

function fmtDate(iso: string) {
  const d = new Date(iso);
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

const selectStyle: React.CSSProperties = {
  background: "var(--surface-3)",
  border: "1px solid var(--line)",
  color: "var(--ink)",
  fontFamily: "var(--font-mono), monospace",
  fontSize: "var(--fs-xs)",
  padding: "4px 6px",
};

/** A saved gem in the list view. Shows the FROZEN score snapshot from save
 * time, your tags + note, stage control, and unsave. */
export function SavedCard({ gem, selected, onToggleSelect }: { gem: SavedGem; selected: boolean; onToggleSelect: () => void }) {
  const snap = gem.scoreSnapshot;
  const axes = { scaling_velocity: snap.scalingVelocity, under_radar: snap.underRadar, funnel_intent: snap.funnelIntent };

  return (
    <div style={{ border: "1.5px solid var(--ink)", background: "var(--paper)", boxShadow: "var(--shadow-hard)", padding: 16, display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ display: "flex", gap: 12, alignItems: "flex-start" }}>
        <input type="checkbox" checked={selected} onChange={onToggleSelect} aria-label="pick to compare" style={{ accentColor: "var(--ink)", marginTop: 4 }} />
        <ScoreBadge score={snap.gemScore} />
        <AppIcon src={gem.iconUrl} name={gem.appName ?? gem.pageName} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <Link href={`/gems/${gem.advertiserId}`} className="t-lg w-heavy caps" style={{ color: "var(--ink)", textDecoration: "none", wordBreak: "break-word", display: "block" }}>
            {gem.appName ?? gem.pageName}
          </Link>
          <div className="eyebrow" style={{ marginTop: 2 }}>{gem.cohort}</div>
        </div>
        <button type="button" onClick={() => unsave(gem.advertiserId)} aria-label="remove from saved" style={{ background: "transparent", border: "none", cursor: "pointer", color: "var(--muted)", lineHeight: 0 }}>
          <IconX size={16} />
        </button>
      </div>

      <SignalChips chips={signalChips(axes)} compact />

      {gem.tags.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
          {gem.tags.map((t) => (
            <span key={t} className="font-mono" style={{ fontSize: "var(--fs-xs)", color: "var(--muted)", border: "1px solid var(--line)", padding: "1px 6px" }}>#{t}</span>
          ))}
        </div>
      )}

      {gem.note ? (
        <p className="t-sm" style={{ color: "var(--ink-soft)", margin: 0, display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>{gem.note}</p>
      ) : (
        <Link href={`/gems/${gem.advertiserId}`} className="t-sm" style={{ color: "var(--muted)" }}>no note yet. why this gem good? write it.</Link>
      )}

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, marginTop: "auto" }}>
        <label className="eyebrow" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          stage
          <select value={gem.stage} onChange={(e) => setStage(gem.advertiserId, e.target.value as Stage)} style={selectStyle}>
            {STAGES.map((s) => (
              <option key={s.id} value={s.id}>{s.label}</option>
            ))}
          </select>
        </label>
        <span className="font-mono nums" style={{ fontSize: "var(--fs-xs)", color: "var(--muted)" }}>saved {fmtDate(gem.savedAt)}</span>
      </div>
    </div>
  );
}
