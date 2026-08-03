"use client";

import { useEffect, useRef, useState } from "react";
import { IconChevronDown, IconHeart, IconHeartFilled } from "@tabler/icons-react";
import type { ShortlistRow } from "@/types/contracts";
import {
  likedPendingKey,
  membershipKey,
  save,
  sortGroups,
  unsave,
  type SavedGem,
} from "@/lib/saved";
import {
  addToFolder,
  rateLike,
  removeFromFolder,
  unrateLike,
  useLikedGems,
  usePendingMemberships,
  useSavedGem,
  useSavedGroups,
} from "@/lib/useSaved";

/** The Liked row uses folder-id 0 in the local failed-row set (never real). */
const LIKED_ROW = 0;

const rowStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  width: "100%",
  textAlign: "left",
  background: "transparent",
  border: "none",
  color: "var(--ink)",
  fontSize: "var(--fs-xs)",
  padding: "7px 9px",
  cursor: "pointer",
};

function seedFromGem(gem: SavedGem) {
  return {
    pageName: gem.pageName,
    appName: gem.appName,
    iconUrl: gem.iconUrl,
    gemScore: gem.scoreSnapshot.gemScore,
  };
}

function seedFromRow(row: ShortlistRow) {
  return {
    pageName: row.page_name,
    appName: row.app_name,
    iconUrl: row.icon_url,
    gemScore: row.gem_score,
  };
}

/**
 * The unified folder picker (Spotify model): "Liked" first (rating toggle),
 * every user folder as a membership checkbox, and "Remove from saved" at the
 * bottom for saved gems. One popover shared by every heart in the product and
 * by the /saved cards' "Add to folder" control. Fixed-position (immune to
 * table/grid clipping), dismissed on outside click / Escape / scroll, arrow
 * keyboard nav across rows. All toggles ride the store's pending set with the
 * same pending/settle/failure feedback.
 */
export function FolderPicker({
  advertiserId,
  row,
  variant,
  size = "md",
}: {
  advertiserId: number;
  /** Needed by the heart variant to save+like an unsaved gem on press. */
  row?: ShortlistRow;
  variant: "heart" | "label";
  size?: "sm" | "md";
}) {
  const gem = useSavedGem(advertiserId);
  const folders = sortGroups(useSavedGroups());
  const liked = useLikedGems();
  const pending = usePendingMemberships();
  const isLiked = liked.some((l) => l.advertiserId === advertiserId);

  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  const [failed, setFailed] = useState<ReadonlySet<number>>(new Set());
  const wrapRef = useRef<HTMLSpanElement>(null);
  const btnRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  const rows = () =>
    Array.from(
      menuRef.current?.querySelectorAll<HTMLButtonElement>(
        '[role="menuitemcheckbox"], [role="menuitem"]',
      ) ?? [],
    );

  const onMenuKey = (e: React.KeyboardEvent) => {
    const list = rows();
    if (list.length === 0) return;
    const idx = list.indexOf(document.activeElement as HTMLButtonElement);
    let next: number | null = null;
    if (e.key === "ArrowDown") next = idx < 0 ? 0 : (idx + 1) % list.length;
    else if (e.key === "ArrowUp") next = idx < 0 ? list.length - 1 : (idx - 1 + list.length) % list.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = list.length - 1;
    if (next != null) {
      e.preventDefault();
      list[next]?.focus();
    }
  };

  useEffect(() => {
    if (!open) return;
    const close = () => setOpen(false);
    const onDown = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) close();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        close();
        btnRef.current?.focus();
      }
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    window.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("resize", close);
    };
  }, [open]);

  const setRowFailed = (rowId: number, isFailed: boolean) =>
    setFailed((prev) => {
      const next = new Set(prev);
      if (isFailed) next.add(rowId);
      else next.delete(rowId);
      return next;
    });

  const openPicker = () => {
    const rect = btnRef.current?.getBoundingClientRect();
    if (!rect) return;
    const width = 200;
    setPos({
      top: rect.bottom + 4,
      left: Math.min(rect.left, window.innerWidth - width - 8),
    });
    setOpen(true);
    requestAnimationFrame(() => rows()[0]?.focus());
  };

  const onTrigger = () => {
    if (open) {
      setOpen(false);
      return;
    }
    // A heart press on an unsaved gem saves + likes optimistically, then the
    // picker opens over the already-updated state (respond on press).
    if (variant === "heart" && !gem && row) {
      save(row);
      rateLike(advertiserId, seedFromRow(row)).catch(() => setRowFailed(LIKED_ROW, true));
    }
    openPicker();
  };

  const toggleLiked = () => {
    setRowFailed(LIKED_ROW, false);
    const seed = gem ? seedFromGem(gem) : row ? seedFromRow(row) : null;
    const act = isLiked ? unrateLike(advertiserId) : seed ? rateLike(advertiserId, seed) : Promise.resolve();
    act.catch(() => setRowFailed(LIKED_ROW, true));
  };

  const toggleMembership = (folderId: number, member: boolean) => {
    setRowFailed(folderId, false);
    const act = member ? removeFromFolder : addToFolder;
    act(advertiserId, folderId).catch(() => setRowFailed(folderId, true));
  };

  const checkboxShell: React.CSSProperties = {
    width: 12,
    height: 12,
    flexShrink: 0,
    border: "1px solid var(--ink)",
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
  };

  const pickerRow = (
    rowId: number,
    label: React.ReactNode,
    checked: boolean,
    onToggle: () => void,
    icon?: React.ReactNode,
  ) => {
    const isPending = pending.has(
      rowId === LIKED_ROW ? likedPendingKey(advertiserId) : membershipKey(advertiserId, rowId),
    );
    const isFailed = failed.has(rowId);
    return (
      <button
        key={rowId}
        type="button"
        role="menuitemcheckbox"
        aria-checked={checked}
        aria-busy={isPending || undefined}
        onClick={onToggle}
        className={`font-mono fp-row${isFailed ? " fp-shake" : ""}`}
        style={{ ...rowStyle, opacity: isPending ? 0.55 : 1 }}
      >
        <span aria-hidden style={checkboxShell}>
          {isPending ? <span className="fp-pending-dot" /> : checked && <span className="fp-fill" />}
        </span>
        {icon}
        <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {label}
          {isFailed && <span style={{ display: "block", color: "var(--muted)" }}>Couldn&apos;t save.</span>}
        </span>
      </button>
    );
  };

  const small = size === "sm";
  const filled = variant === "heart" ? !!gem : false;

  return (
    <span ref={wrapRef} style={{ display: "inline-flex" }}>
      {variant === "heart" ? (
        <button
          ref={btnRef}
          type="button"
          aria-pressed={filled}
          aria-haspopup="menu"
          aria-expanded={open}
          aria-label={filled ? "Saved — folders" : "Save this gem"}
          title={filled ? "Folders" : "Save this gem"}
          onClick={(e) => {
            e.stopPropagation();
            e.preventDefault();
            onTrigger();
          }}
          className="fp-press"
          style={{
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            cursor: "pointer",
            padding: small ? 4 : 6,
            border: "1.5px solid var(--ink)",
            background: filled ? "var(--ink)" : "transparent",
            color: filled ? "var(--bg)" : "var(--ink)",
          }}
        >
          {filled ? <IconHeartFilled size={small ? 14 : 16} /> : <IconHeart size={small ? 14 : 16} stroke={1.75} />}
        </button>
      ) : (
        <button
          ref={btnRef}
          type="button"
          aria-haspopup="menu"
          aria-expanded={open}
          onClick={onTrigger}
          className="font-mono fp-press"
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 4,
            background: "var(--surface-3)",
            border: "1px solid var(--line)",
            color: "var(--ink)",
            fontSize: "var(--fs-xs)",
            padding: "4px 6px",
            cursor: "pointer",
          }}
        >
          Add to folder
          <IconChevronDown size={12} aria-hidden style={{ color: "var(--muted)" }} />
        </button>
      )}
      {open && pos && (
        <div
          ref={menuRef}
          role="menu"
          aria-label="Folders"
          className="dd-panel"
          onKeyDown={onMenuKey}
          style={{
            position: "fixed",
            top: pos.top,
            left: pos.left,
            width: 200,
            maxHeight: 280,
            overflowY: "auto",
            zIndex: 50,
            padding: 4,
            background: "var(--surface-4)",
            border: "1.5px solid var(--ink)",
            boxShadow: "var(--shadow-hard)",
          }}
        >
          <style>{`
            .fp-press { transition: transform 0.15s ease-out; }
            .fp-press:active { transform: scale(0.97); }
            .fp-row { transition: opacity 0.15s ease-out; }
            .fp-fill {
              width: 100%;
              height: 100%;
              background: var(--ink);
              transform: scale(1);
              opacity: 1;
              transition: transform 0.2s ease-out, opacity 0.2s ease-out;
            }
            @starting-style {
              .fp-fill { transform: scale(0.9); opacity: 0; }
            }
            .fp-pending-dot {
              width: 6px;
              height: 6px;
              background: var(--muted);
            }
            @keyframes fp-shake {
              0% { transform: translateX(0); }
              12.5% { transform: translateX(-3px); }
              37.5% { transform: translateX(3px); }
              62.5% { transform: translateX(-3px); }
              87.5% { transform: translateX(3px); }
              100% { transform: translateX(0); }
            }
            .fp-shake { animation: fp-shake 0.25s ease-out; }
            @media (prefers-reduced-motion: reduce) {
              .fp-press { transition: none; }
              .fp-press:active { transform: none; }
              .fp-fill { transition: opacity 0.2s ease-out; transform: none; }
              @starting-style {
                .fp-fill { transform: none; opacity: 0; }
              }
              .fp-shake { animation: none; }
            }
          `}</style>
          {pickerRow(
            LIKED_ROW,
            "Liked",
            isLiked,
            toggleLiked,
            <IconHeart size={13} aria-hidden style={{ flexShrink: 0, color: "var(--ink)" }} />,
          )}
          {gem &&
            folders.map((f) =>
              pickerRow(f.id, f.name, gem.groupIds.includes(f.id), () =>
                toggleMembership(f.id, gem.groupIds.includes(f.id)),
              ),
            )}
          {gem && (
            <>
              <div aria-hidden style={{ borderTop: "1px solid var(--line)", margin: "4px 0" }} />
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  setOpen(false);
                  unsave(advertiserId);
                }}
                className="font-mono fp-row"
                style={rowStyle}
              >
                Remove from saved
              </button>
            </>
          )}
        </div>
      )}
    </span>
  );
}
