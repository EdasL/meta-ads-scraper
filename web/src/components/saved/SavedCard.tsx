"use client";

import Link from "next/link";
import { IconX } from "@tabler/icons-react";
import type { SavedGem, SavedGroup } from "@/lib/saved";
import { setGroup, unsave } from "@/lib/useSaved";
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
  maxWidth: 130,
};

/** A saved gem card — same minimal shape as the Discover card (logo + name +
 * the ad metric), plus your tags + note, folder control, and unsave. The
 * metric is the FROZEN ad count from save time. */
export function SavedCard({ gem, folders }: { gem: SavedGem; folders: SavedGroup[] }) {
  const snap = gem.scoreSnapshot;
  const name = gem.appName ?? gem.pageName;
  const filed = gem.groupId != null && folders.some((f) => f.id === gem.groupId);

  return (
    <div style={{ border: "1.5px solid var(--ink)", background: "var(--paper)", boxShadow: "var(--shadow-hard)", padding: 10, display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
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
        {folders.length > 0 ? (
          <label className="eyebrow" style={{ display: "inline-flex", alignItems: "center", gap: 6, minWidth: 0 }}>
            Folder
            <select
              value={filed ? String(gem.groupId) : "0"}
              onChange={(e) => setGroup(gem.advertiserId, Number(e.target.value))}
              aria-label="Folder"
              style={selectStyle}
            >
              <option value="0">No folder</option>
              {folders.map((f) => (
                <option key={f.id} value={f.id}>{f.name}</option>
              ))}
            </select>
          </label>
        ) : (
          <span />
        )}
        <span className="font-mono nums" style={{ fontSize: "var(--fs-xs)", color: "var(--muted)", whiteSpace: "nowrap" }}>Saved {fmtDate(gem.savedAt)}</span>
      </div>
    </div>
  );
}
