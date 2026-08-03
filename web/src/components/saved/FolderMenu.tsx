"use client";

import { useEffect, useRef, useState } from "react";
import { IconDots } from "@tabler/icons-react";

/** Folder ⋯ menu (Delete folder). Fixed-position so ancestors can't clip it;
 * scroll/resize close it (the anchor moves away). Arrow/Home/End keyboard nav;
 * Escape closes and refocuses the trigger. */
export function FolderMenu({ onDelete }: { onDelete: () => void }) {
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
        style={{
          display: "inline-flex",
          alignItems: "center",
          background: "transparent",
          border: "1px solid var(--line)",
          color: "var(--ink)",
          cursor: "pointer",
          padding: "2px 6px",
          lineHeight: 0,
        }}
      >
        <IconDots size={14} />
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
