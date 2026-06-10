"use client";

import { useEffect, useRef, useState } from "react";

/**
 * Freeform note. Debounced autosave + save on blur — never a blocking modal.
 * Prose lives at 16px for readability (spec §4.2).
 *
 * Initializes from `value` once; to reset across gems the parent passes a
 * `key` (e.g. the advertiser id) so React remounts it with fresh state.
 */
export function NoteEditor({
  value,
  onSave,
  placeholder = "Why did you save this?",
}: {
  value: string;
  onSave: (v: string) => void;
  placeholder?: string;
}) {
  const [text, setText] = useState(value);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);

  function onChange(v: string) {
    setText(v);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => onSave(v), 500);
  }

  return (
    <textarea
      value={text}
      onChange={(e) => onChange(e.target.value)}
      onBlur={() => onSave(text)}
      placeholder={placeholder}
      className="t-md"
      style={{
        width: "100%",
        minHeight: 84,
        resize: "vertical",
        background: "var(--surface-3)",
        border: "1px solid var(--line)",
        color: "var(--ink)",
        padding: "10px 12px",
        outline: "none",
        lineHeight: 1.5,
        fontFamily: "var(--font-sans), system-ui, sans-serif",
      }}
    />
  );
}
