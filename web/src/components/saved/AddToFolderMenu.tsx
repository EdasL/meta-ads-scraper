"use client";

import { useEffect, useRef, useState } from "react";
import { IconChevronDown } from "@tabler/icons-react";
import { membershipKey, sortGroups, type SavedGem } from "@/lib/saved";
import { addToFolder, removeFromFolder, usePendingMemberships, useSavedGroups } from "@/lib/useSaved";

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
  const pending = usePendingMemberships();
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  // Rows whose last write FAILED ("Couldn't save." until the next attempt).
  const [failed, setFailed] = useState<ReadonlySet<number>>(new Set());
  const wrapRef = useRef<HTMLSpanElement>(null);
  const btnRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  const setRowFailed = (folderId: number, isFailed: boolean) =>
    setFailed((prev) => {
      const next = new Set(prev);
      if (isFailed) next.add(folderId);
      else next.delete(folderId);
      return next;
    });

  const toggleMembership = (folderId: number, member: boolean) => {
    setRowFailed(folderId, false);
    const act = member ? removeFromFolder : addToFolder;
    act(gem.advertiserId, folderId).catch(() => setRowFailed(folderId, true));
  };

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
          <style>{`
            .atf-row { transition: opacity 0.15s ease-out; }
            .atf-fill {
              width: 100%;
              height: 100%;
              background: var(--ink);
              transform: scale(1);
              opacity: 1;
              transition: transform 0.2s ease-out, opacity 0.2s ease-out;
            }
            @starting-style {
              .atf-fill { transform: scale(0.9); opacity: 0; }
            }
            .atf-pending-dot {
              width: 6px;
              height: 6px;
              background: var(--muted);
            }
            @keyframes atf-shake {
              0% { transform: translateX(0); }
              12.5% { transform: translateX(-3px); }
              37.5% { transform: translateX(3px); }
              62.5% { transform: translateX(-3px); }
              87.5% { transform: translateX(3px); }
              100% { transform: translateX(0); }
            }
            .atf-shake { animation: atf-shake 0.25s ease-out; }
            @media (prefers-reduced-motion: reduce) {
              .atf-fill { transition: opacity 0.2s ease-out; transform: none; }
              @starting-style {
                .atf-fill { transform: none; opacity: 0; }
              }
              .atf-shake { animation: none; }
            }
          `}</style>
          {folders.map((f) => {
            const member = gem.groupIds.includes(f.id);
            const isPending = pending.has(membershipKey(gem.advertiserId, f.id));
            const isFailed = failed.has(f.id);
            return (
              <button
                key={f.id}
                type="button"
                role="menuitemcheckbox"
                aria-checked={member}
                aria-busy={isPending || undefined}
                onClick={() => toggleMembership(f.id, member)}
                className={`font-mono atf-row${isFailed ? " atf-shake" : ""}`}
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
                  opacity: isPending ? 0.55 : 1,
                }}
              >
                <span
                  aria-hidden
                  style={{
                    width: 12,
                    height: 12,
                    flexShrink: 0,
                    border: "1px solid var(--ink)",
                    display: "inline-flex",
                    alignItems: "center",
                    justifyContent: "center",
                  }}
                >
                  {isPending ? <span className="atf-pending-dot" /> : member && <span className="atf-fill" />}
                </span>
                <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {f.name}
                  {isFailed && (
                    <span style={{ display: "block", color: "var(--muted)" }}>Couldn&apos;t save.</span>
                  )}
                </span>
              </button>
            );
          })}
        </div>
      )}
    </span>
  );
}
