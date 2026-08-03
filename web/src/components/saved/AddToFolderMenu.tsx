"use client";

import { useEffect, useRef, useState } from "react";
import { IconChevronDown } from "@tabler/icons-react";
import { sortGroups, type SavedGem } from "@/lib/saved";
import { addToFolder, removeFromFolder, useSavedGroups } from "@/lib/useSaved";

/**
 * "Add to folder" — the membership control (playlist model). Opens the house
 * menu as a checkbox list: one `menuitemcheckbox` per folder, toggling calls
 * add/removeFromFolder and the menu STAYS open (several toggles in one visit).
 * Renders nothing when the user has no folders (create them on /saved).
 * Fixed-position panel + close-on-scroll, keyboard nav shared with the folder
 * menu (arrows wrap, Home/End, Esc closes and refocuses the trigger).
 */
export function AddToFolderMenu({ gem }: { gem: SavedGem }) {
  const folders = sortGroups(useSavedGroups());
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  const wrapRef = useRef<HTMLSpanElement>(null);
  const btnRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  const items = () =>
    Array.from(menuRef.current?.querySelectorAll<HTMLButtonElement>('[role="menuitemcheckbox"]') ?? []);

  const onMenuKey = (e: React.KeyboardEvent) => {
    const list = items();
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

  if (folders.length === 0) return null;

  const toggle = () => {
    if (open) {
      setOpen(false);
      return;
    }
    const rect = btnRef.current?.getBoundingClientRect();
    if (!rect) return;
    const width = 200;
    setPos({
      top: rect.bottom + 4,
      left: Math.min(rect.left, window.innerWidth - width - 8),
    });
    setOpen(true);
    requestAnimationFrame(() => items()[0]?.focus());
  };

  return (
    <span ref={wrapRef} style={{ display: "inline-flex" }}>
      <button
        ref={btnRef}
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={toggle}
        className="font-mono"
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
            maxHeight: 260,
            overflowY: "auto",
            zIndex: 50,
            padding: 4,
            background: "var(--surface-4)",
            border: "1.5px solid var(--ink)",
            boxShadow: "var(--shadow-hard)",
          }}
        >
          {folders.map((f) => {
            const member = gem.groupIds.includes(f.id);
            return (
              <button
                key={f.id}
                type="button"
                role="menuitemcheckbox"
                aria-checked={member}
                onClick={() =>
                  member ? removeFromFolder(gem.advertiserId, f.id) : addToFolder(gem.advertiserId, f.id)
                }
                className="font-mono"
                style={{
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
                }}
              >
                <span
                  aria-hidden
                  style={{
                    width: 12,
                    height: 12,
                    flexShrink: 0,
                    border: "1px solid var(--ink)",
                    background: member ? "var(--ink)" : "transparent",
                  }}
                />
                <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {f.name}
                </span>
              </button>
            );
          })}
        </div>
      )}
    </span>
  );
}
