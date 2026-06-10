"use client";

import { useEffect, useId, useRef, useState } from "react";
import { IconHelpCircle } from "@tabler/icons-react";

/**
 * Inline glossary dot. Click (or focus) reveals a one-line definition for a
 * proprietary term — keeps jargon out of the main read while still teachable.
 * Closes on outside-click or Escape. No browser dialogs.
 */
export function InfoDot({ text, label = "what this means" }: { text: string; label?: string }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);
  const tipId = useId();

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <span ref={ref} style={{ position: "relative", display: "inline-flex" }}>
      <button
        type="button"
        aria-label={label}
        aria-expanded={open}
        aria-describedby={open ? tipId : undefined}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((o) => !o);
        }}
        style={{
          display: "inline-flex",
          alignItems: "center",
          background: "transparent",
          border: "none",
          padding: 0,
          cursor: "pointer",
          color: "var(--muted)",
          lineHeight: 0,
        }}
      >
        <IconHelpCircle size={15} stroke={1.75} />
      </button>
      {open && (
        <span
          id={tipId}
          role="tooltip"
          className="t-xs"
          style={{
            position: "absolute",
            top: "150%",
            left: 0,
            zIndex: 60,
            width: 230,
            padding: "9px 11px",
            border: "1.5px solid var(--ink)",
            background: "var(--surface-4)",
            color: "var(--ink-soft)",
            boxShadow: "var(--shadow-hard)",
            lineHeight: 1.45,
          }}
        >
          {text}
        </span>
      )}
    </span>
  );
}
