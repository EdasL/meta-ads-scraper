"use client";

import { IconAd, IconFolder, IconFolderPlus, IconHeart } from "@tabler/icons-react";
import type { SavedGroup } from "@/lib/saved";

const tile: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  border: "1.5px solid var(--ink)",
  background: "var(--paper)",
  color: "var(--ink)",
  padding: "10px 12px",
  cursor: "pointer",
  minWidth: 0,
  textAlign: "left",
};

/** The "Folders" tile row on /saved: the built-in Liked and Ads collections
 * pinned first, then one tile per folder (name + gem count), then a New folder
 * tile. Tiles navigate into the folder view. */
export function FolderTiles({
  folders,
  counts,
  likedCount,
  adsCount,
  keyFor,
  onOpen,
  onOpenLiked,
  onOpenAds,
  onCreate,
}: {
  folders: SavedGroup[];
  counts: Map<number, number>;
  likedCount: number;
  adsCount: number;
  keyFor: (id: number) => number;
  onOpen: (id: number) => void;
  onOpenLiked: () => void;
  onOpenAds: () => void;
  onCreate: () => void;
}) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))", gap: 10, marginBottom: 24 }}>
      <style>{`
        .ft-press { transition: transform 0.15s ease-out; }
        .ft-press:active { transform: scale(0.97); }
        @media (prefers-reduced-motion: reduce) {
          .ft-press { transition: none; }
          .ft-press:active { transform: none; }
        }
      `}</style>
      <button type="button" onClick={onOpenLiked} className="ft-press" style={tile}>
        <IconHeart size={16} aria-hidden style={{ flexShrink: 0 }} />
        <span className="t-sm w-strong" style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          Liked
        </span>
        <span className="nums t-xs" style={{ color: "var(--muted)", flexShrink: 0 }}>{likedCount}</span>
      </button>
      <button type="button" onClick={onOpenAds} className="ft-press" style={tile}>
        <IconAd size={16} aria-hidden style={{ flexShrink: 0 }} />
        <span className="t-sm w-strong" style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          Ads
        </span>
        <span className="nums t-xs" style={{ color: "var(--muted)", flexShrink: 0 }}>{adsCount}</span>
      </button>
      {folders.map((f) => (
        <button
          key={keyFor(f.id)}
          type="button"
          onClick={() => onOpen(f.id)}
          className="ft-press"
          style={tile}
        >
          <IconFolder size={16} aria-hidden style={{ flexShrink: 0 }} />
          <span className="t-sm w-strong" style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {f.name}
          </span>
          <span className="nums t-xs" style={{ color: "var(--muted)", flexShrink: 0 }}>{counts.get(f.id) ?? 0}</span>
        </button>
      ))}
      <button type="button" onClick={onCreate} className="ft-press" style={{ ...tile, background: "transparent" }}>
        <IconFolderPlus size={16} aria-hidden style={{ flexShrink: 0 }} />
        <span className="t-sm w-strong">New folder</span>
      </button>
    </div>
  );
}
