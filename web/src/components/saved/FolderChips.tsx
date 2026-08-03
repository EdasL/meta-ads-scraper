"use client";

import { useEffect, useRef, useState } from "react";
import { IconDots } from "@tabler/icons-react";
import type { SavedGroup } from "@/lib/saved";
import { renameGroup } from "@/lib/useSaved";

const chipBase: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
  border: "1px solid var(--line)",
  background: "transparent",
  color: "var(--ink)",
  fontFamily: "var(--font-mono), monospace",
  fontSize: "var(--fs-xs)",
  padding: "5px 10px",
  cursor: "pointer",
  whiteSpace: "nowrap",
};

const chipActive: React.CSSProperties = {
  ...chipBase,
  background: "var(--ink)",
  color: "var(--bg)",
  borderColor: "var(--ink)",
};

/** Folder ⋯ menu (Delete folder). Fixed-position so ancestors can't clip it;
 * scroll/resize close it (the anchor moves away). Arrow/Home/End keyboard nav
 * across enabled items; Escape closes and refocuses the trigger. */
function FolderMenu({ active, onDelete }: { active: boolean; onDelete: () => void }) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  const wrapRef = useRef<HTMLSpanElement>(null);
  const btnRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  const enabledItems = () =>
    Array.from(menuRef.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]:not(:disabled)') ?? []);

  const onMenuKey = (e: React.KeyboardEvent) => {
    const items = enabledItems();
    if (items.length === 0) return;
    const idx = items.indexOf(document.activeElement as HTMLButtonElement);
    let next: number | null = null;
    if (e.key === "ArrowDown") next = idx < 0 ? 0 : (idx + 1) % items.length;
    else if (e.key === "ArrowUp") next = idx < 0 ? items.length - 1 : (idx - 1 + items.length) % items.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = items.length - 1;
    if (next != null) {
      e.preventDefault();
      items[next]?.focus();
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

  const toggle = () => {
    if (open) {
      setOpen(false);
      return;
    }
    const rect = btnRef.current?.getBoundingClientRect();
    if (!rect) return;
    const width = 168;
    setPos({
      top: rect.bottom + 4,
      left: Math.min(rect.left, window.innerWidth - width - 8),
    });
    setOpen(true);
    // Focus the first enabled item once the panel exists (Dropdown's rAF idiom).
    requestAnimationFrame(() => enabledItems()[0]?.focus());
  };

  return (
    <span ref={wrapRef} style={{ display: "inline-flex" }}>
      <button
        ref={btnRef}
        type="button"
        aria-label="Folder options"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={toggle}
        className="fc-press"
        style={{
          display: "inline-flex",
          alignItems: "center",
          background: "transparent",
          border: "none",
          color: active ? "var(--bg)" : "var(--ink)",
          cursor: "pointer",
          padding: 0,
          lineHeight: 0,
        }}
      >
        <IconDots size={13} />
      </button>
      {open && pos && (
        <div
          ref={menuRef}
          role="menu"
          className="dd-panel"
          onKeyDown={onMenuKey}
          style={{
            position: "fixed",
            top: pos.top,
            left: pos.left,
            width: 168,
            zIndex: 50,
            padding: 4,
            background: "var(--surface-4)",
            border: "1.5px solid var(--ink)",
            boxShadow: "var(--shadow-hard)",
          }}
        >
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setOpen(false);
              onDelete();
            }}
            className="font-mono"
            style={{
              display: "block",
              width: "100%",
              textAlign: "left",
              background: "transparent",
              border: "none",
              color: "var(--ink)",
              fontSize: "var(--fs-xs)",
              padding: "7px 9px",
              cursor: "pointer",
            }}
          >
            Delete folder
          </button>
        </div>
      )}
    </span>
  );
}

/** The folder filter row: "All · N", one chip per folder, "New folder" at the
 * end. The active chip is filled; clicking its name renames it inline. */
export function FolderChips({
  folders,
  active,
  editingId,
  counts,
  allCount,
  keyFor,
  onSelect,
  onEditStart,
  onEditEnd,
  onDelete,
  onCreate,
}: {
  folders: SavedGroup[];
  active: number | null;
  editingId: number | null;
  counts: Map<number, number>;
  allCount: number;
  keyFor: (id: number) => number;
  onSelect: (id: number | null) => void;
  onEditStart: (id: number) => void;
  onEditEnd: () => void;
  onDelete: (folder: SavedGroup) => void;
  onCreate: () => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);

  const commit = (folder: SavedGroup) => {
    const name = (inputRef.current?.value ?? "").trim();
    if (name && name !== folder.name) renameGroup(folder.id, name);
    onEditEnd();
  };

  return (
    <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 8, marginBottom: 18 }}>
      <style>{`
        .fc-press { transition: transform 0.15s ease-out; }
        .fc-press:active { transform: scale(0.97); }
        @media (prefers-reduced-motion: reduce) {
          .fc-press { transition: none; }
          .fc-press:active { transform: none; }
        }
      `}</style>
      <button
        type="button"
        onClick={() => onSelect(null)}
        aria-pressed={active === null}
        className="fc-press"
        style={active === null ? chipActive : chipBase}
      >
        All<span className="nums" style={{ opacity: 0.7 }}>· {allCount}</span>
      </button>
      {folders.map((f) => {
        const isActive = active === f.id;
        const editing = editingId === f.id;
        return (
          <span key={keyFor(f.id)} style={isActive ? chipActive : chipBase}>
            {editing ? (
              <input
                ref={inputRef}
                defaultValue={f.name}
                maxLength={60}
                autoFocus
                onFocus={(e) => e.currentTarget.select()}
                onBlur={() => commit(f)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") commit(f);
                  if (e.key === "Escape") onEditEnd();
                }}
                aria-label="Folder name"
                className="font-mono"
                style={{
                  width: 110,
                  background: "var(--surface-3)",
                  border: "1px solid var(--line)",
                  color: "var(--ink)",
                  fontSize: "var(--fs-xs)",
                  padding: "0 4px",
                }}
              />
            ) : (
              <button
                type="button"
                onClick={() => (isActive ? onEditStart(f.id) : onSelect(f.id))}
                title={isActive ? "Rename" : undefined}
                style={{
                  background: "transparent",
                  border: "none",
                  color: "inherit",
                  font: "inherit",
                  padding: 0,
                  cursor: isActive ? "text" : "pointer",
                  maxWidth: 180,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {f.name}
              </button>
            )}
            <span className="nums" style={{ opacity: 0.7 }}>· {counts.get(f.id) ?? 0}</span>
            {isActive && !editing && <FolderMenu active={isActive} onDelete={() => onDelete(f)} />}
          </span>
        );
      })}
      <button type="button" onClick={onCreate} className="t-sm w-strong fc-press" style={{
        background: "transparent",
        border: "1.5px solid var(--ink)",
        color: "var(--ink)",
        padding: "5px 12px",
        cursor: "pointer",
        whiteSpace: "nowrap",
      }}>
        New folder
      </button>
    </div>
  );
}
