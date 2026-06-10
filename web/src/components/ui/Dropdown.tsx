"use client";

import { useEffect, useId, useRef, useState } from "react";
import { IconCheck, IconChevronDown } from "@tabler/icons-react";

export interface DropdownOption {
  value: string;
  label: string;
  /** Muted right-aligned annotation, e.g. a result count. */
  hint?: string;
}

/**
 * Custom select for the filter bar — a real listbox instead of a native
 * <select>, so options can carry counts and the open panel matches the paper
 * theme. Combobox keyboard semantics: focus stays on the trigger, arrows move
 * the active option (aria-activedescendant), Enter/Space pick it. Closes on
 * outside-click or Escape (InfoDot idiom).
 */
export function Dropdown({
  value,
  options,
  onChange,
  ariaLabel,
  minWidth = 150,
}: {
  value: string;
  options: DropdownOption[];
  onChange: (value: string) => void;
  ariaLabel: string;
  minWidth?: number;
}) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const ref = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const listboxId = useId();

  const selectedIdx = Math.max(0, options.findIndex((o) => o.value === value));
  const selected = options[selectedIdx];

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

  // Opening highlights the current value and keeps it in view (the rAF runs
  // after the panel has rendered).
  const openPanel = () => {
    setActive(selectedIdx);
    setOpen(true);
    requestAnimationFrame(() => {
      listRef.current
        ?.querySelector('[data-active="true"]')
        ?.scrollIntoView({ block: "nearest" });
    });
  };

  const choose = (idx: number) => {
    const option = options[idx];
    if (option && option.value !== value) onChange(option.value);
    setOpen(false);
  };

  const move = (idx: number) => {
    setActive(idx);
    requestAnimationFrame(() => {
      listRef.current
        ?.querySelector('[data-active="true"]')
        ?.scrollIntoView({ block: "nearest" });
    });
  };

  const onTriggerKey = (e: React.KeyboardEvent) => {
    if (!open) {
      if (["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key)) {
        e.preventDefault();
        openPanel();
      }
      return;
    }
    switch (e.key) {
      case "ArrowDown":
        e.preventDefault();
        move(Math.min(active + 1, options.length - 1));
        break;
      case "ArrowUp":
        e.preventDefault();
        move(Math.max(active - 1, 0));
        break;
      case "Home":
        e.preventDefault();
        move(0);
        break;
      case "End":
        e.preventDefault();
        move(options.length - 1);
        break;
      case "Enter":
      case " ":
        e.preventDefault();
        choose(active);
        break;
      case "Tab":
        setOpen(false);
        break;
    }
  };

  return (
    <div ref={ref} style={{ position: "relative", display: "inline-flex", minWidth }}>
      <button
        type="button"
        role="combobox"
        aria-expanded={open}
        aria-haspopup="listbox"
        aria-controls={open ? listboxId : undefined}
        aria-activedescendant={open ? `${listboxId}-${active}` : undefined}
        aria-label={ariaLabel}
        onClick={() => (open ? setOpen(false) : openPanel())}
        onKeyDown={onTriggerKey}
        className="font-mono"
        style={{
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 8,
          width: "100%",
          background: "var(--surface-3)",
          border: "1px solid var(--line)",
          color: "var(--ink)",
          fontSize: "var(--fs-xs)",
          padding: "6px 8px",
          cursor: "pointer",
        }}
      >
        <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {selected?.label ?? value}
        </span>
        <IconChevronDown
          size={13}
          stroke={1.75}
          aria-hidden
          style={{
            flexShrink: 0,
            color: "var(--muted)",
            transform: open ? "rotate(180deg)" : undefined,
            transition: "transform 0.12s ease",
          }}
        />
      </button>

      {open && (
        <ul
          ref={listRef}
          id={listboxId}
          role="listbox"
          aria-label={ariaLabel}
          className="dd-panel"
          style={{
            position: "absolute",
            top: "calc(100% + 4px)",
            left: 0,
            zIndex: 50,
            minWidth: "100%",
            maxHeight: 280,
            overflowY: "auto",
            margin: 0,
            padding: 4,
            listStyle: "none",
            background: "var(--surface-4)",
            border: "1.5px solid var(--ink)",
            boxShadow: "var(--shadow-hard)",
          }}
        >
          {options.map((o, i) => (
            <li
              key={o.value}
              id={`${listboxId}-${i}`}
              role="option"
              aria-selected={o.value === value}
              data-active={i === active || undefined}
              onMouseEnter={() => setActive(i)}
              // Keep focus on the trigger so the keyboard model never breaks.
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => choose(i)}
              className="font-mono"
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                padding: "7px 9px",
                fontSize: "var(--fs-xs)",
                cursor: "pointer",
                background: i === active ? "var(--surface-3)" : "transparent",
                color: "var(--ink)",
                whiteSpace: "nowrap",
              }}
            >
              <span aria-hidden style={{ width: 13, flexShrink: 0, display: "inline-flex" }}>
                {o.value === value && <IconCheck size={13} stroke={2} />}
              </span>
              <span style={{ flex: 1 }}>{o.label}</span>
              {o.hint && (
                <span className="nums" style={{ color: "var(--muted)", marginLeft: 12 }}>
                  {o.hint}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
