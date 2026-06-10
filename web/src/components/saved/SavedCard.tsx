"use client";

import Link from "next/link";
import { IconX } from "@tabler/icons-react";
import { STAGES, type SavedGem, type Stage } from "@/lib/saved";
import { setStage, unsave } from "@/lib/useSaved";
import { AppIcon, Trend } from "@/components/ui";

function fmtDate(iso: string) {
  return new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

const selectStyle: React.CSSProperties = {
  background: "var(--surface-3)",
  border: "1px solid var(--line)",
  color: "var(--ink)",
  fontFamily: "var(--font-mono), monospace",
  fontSize: "var(--fs-xs)",
  padding: "4px 6px",
};

/** A saved gem in the list view — same minimal shape as the Discover card
 * (logo + name + the ad metric), plus your tags + note, stage control, and
 * unsave. The metric is the FROZEN ad count from save time. */
export function SavedCard({ gem, selected, onToggleSelect }: { gem: SavedGem; selected: boolean; onToggleSelect: () => void }) {
  const snap = gem.scoreSnapshot;
  const name = gem.appName ?? gem.pageName;

  return (
    <div style={{ border: "1.5px solid var(--ink)", background: "var(--paper)", boxShadow: "var(--shadow-hard)", padding: 10, display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
        <input type="checkbox" checked={selected} onChange={onToggleSelect} aria-label="pick to compare" style={{ accentColor: "var(--ink)", marginTop: 4 }} />
        <AppIcon src={gem.iconUrl} name={name} size={44} />
        <Link
          href={`/gems/${gem.advertiserId}`}
          className="t-sm w-strong"
          style={{
            flex: 1,
            minWidth: 0,
            color: "var(--ink)",
            textDecoration: "none",
            display: "-webkit-box",
            WebkitLineClamp: 2,
            WebkitBoxOrient: "vertical",
            overflow: "hidden",
            wordBreak: "break-word",
            // Same reserved two lines as the Discover card so the metric row
            // sits at the same height on every card.
            minHeight: "calc(2 * var(--lh-sm))",
          }}
        >
          {name}
        </Link>
        <button type="button" onClick={() => unsave(gem.advertiserId)} aria-label="remove from saved" style={{ background: "transparent", border: "none", cursor: "pointer", color: "var(--muted)", lineHeight: 0 }}>
          <IconX size={16} />
        </button>
      </div>

      <div>
        <div className="eyebrow" style={{ marginBottom: 2 }}>Ads at save</div>
        <Trend glyph={null} label={`${snap.activeAdCount} ads live`} tone="flat" />
      </div>

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
        <Link href={`/gems/${gem.advertiserId}`} className="t-sm" style={{ color: "var(--muted)" }}>No note yet — add one.</Link>
      )}

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, marginTop: "auto", paddingTop: 8, borderTop: "1px solid var(--line)" }}>
        <label className="eyebrow" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          Stage
          <select value={gem.stage} onChange={(e) => setStage(gem.advertiserId, e.target.value as Stage)} style={selectStyle}>
            {STAGES.map((s) => (
              <option key={s.id} value={s.id}>{s.label}</option>
            ))}
          </select>
        </label>
        <span className="font-mono nums" style={{ fontSize: "var(--fs-xs)", color: "var(--muted)", whiteSpace: "nowrap" }}>Saved {fmtDate(gem.savedAt)}</span>
      </div>
    </div>
  );
}
