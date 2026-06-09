"use client";

import { useMemo, useRef, useState, type KeyboardEvent } from "react";
import { IconX } from "@tabler/icons-react";
import { normalizeTag, type TagCount } from "@/lib/saved";

/**
 * Creatable autocomplete for tags. Suggests prior tags as you type, lets you
 * add a new one inline, and is keyboard-driven (↑/↓ to move, Enter to commit,
 * Backspace on empty to remove the last tag). Capture now, structure later.
 */
type Option = { kind: "existing"; value: string; uses: number } | { kind: "new"; value: string };

export function TagInput({
  tags,
  onAdd,
  onRemove,
  suggestions,
  placeholder = "add tag…",
}: {
  tags: string[];
  onAdd: (raw: string) => void;
  onRemove: (tag: string) => void;
  suggestions: TagCount[];
  placeholder?: string;
}) {
  const [q, setQ] = useState("");
  const [hi, setHi] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const nq = normalizeTag(q);

  const options = useMemo<Option[]>(() => {
    const existing = new Set(tags);
    const matches: Option[] = suggestions
      .filter((s) => !existing.has(s.name) && (nq === "" || s.name.includes(nq)))
      .slice(0, 6)
      .map((s) => ({ kind: "existing", value: s.name, uses: s.uses }));
    const exact = suggestions.some((s) => s.name === nq) || tags.includes(nq);
    if (nq && !exact) matches.push({ kind: "new", value: nq });
    return matches;
  }, [suggestions, tags, nq]);

  function commit(value: string) {
    const v = normalizeTag(value);
    if (!v) return;
    onAdd(v);
    setQ("");
    setHi(0);
    inputRef.current?.focus();
  }

  function onKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHi((h) => Math.min(h + 1, Math.max(options.length - 1, 0)));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHi((h) => Math.max(h - 1, 0));
    } else if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      commit(options[hi]?.value ?? nq);
    } else if (e.key === "Backspace" && q === "" && tags.length) {
      onRemove(tags[tags.length - 1]);
    } else if (e.key === "Escape") {
      setQ("");
    }
  }

  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 6, alignItems: "center" }}>
      {tags.map((t) => (
        <span
          key={t}
          className="font-mono"
          style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: "var(--fs-xs)", border: "1px solid var(--ink)", padding: "2px 4px 2px 8px" }}
        >
          #{t}
          <button
            type="button"
            aria-label={`remove ${t}`}
            onClick={() => onRemove(t)}
            style={{ background: "transparent", border: "none", cursor: "pointer", color: "var(--muted)", lineHeight: 0, padding: 0 }}
          >
            <IconX size={12} />
          </button>
        </span>
      ))}
      <div style={{ position: "relative", flex: "1 1 120px", minWidth: 120 }}>
        <input
          ref={inputRef}
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setHi(0);
          }}
          onKeyDown={onKeyDown}
          placeholder={placeholder}
          className="font-mono"
          style={{
            width: "100%",
            background: "transparent",
            border: "none",
            borderBottom: "1px solid var(--line)",
            color: "var(--ink)",
            fontSize: "var(--fs-xs)",
            padding: "4px 2px",
            outline: "none",
          }}
        />
        {options.length > 0 && q !== "" && (
          <ul
            style={{
              position: "absolute",
              top: "100%",
              left: 0,
              right: 0,
              zIndex: 40,
              listStyle: "none",
              margin: 0,
              padding: 0,
              border: "1.5px solid var(--ink)",
              background: "var(--surface-4)",
              maxHeight: 200,
              overflowY: "auto",
            }}
          >
            {options.map((o, i) => (
              <li key={o.kind + o.value}>
                <button
                  type="button"
                  onMouseDown={(e) => {
                    e.preventDefault();
                    commit(o.value);
                  }}
                  onMouseEnter={() => setHi(i)}
                  className="font-mono"
                  style={{
                    display: "flex",
                    width: "100%",
                    justifyContent: "space-between",
                    gap: 8,
                    textAlign: "left",
                    padding: "6px 9px",
                    fontSize: "var(--fs-xs)",
                    background: i === hi ? "var(--ink)" : "transparent",
                    color: i === hi ? "var(--bg)" : "var(--ink)",
                    border: "none",
                    cursor: "pointer",
                  }}
                >
                  <span>{o.kind === "new" ? `add new "${o.value}"` : `#${o.value}`}</span>
                  {o.kind === "existing" && <span style={{ opacity: 0.6 }}>{o.uses}</span>}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
