"use client";

import { useState, useSyncExternalStore } from "react";
import { IconCheck, IconX } from "@tabler/icons-react";
import { useSavedList } from "@/lib/useSaved";
import { checklistSteps, dismissChecklist, readFlags, type OnboardingFlags } from "@/lib/onboarding";

// "Are we on the client yet?" — SSR-safe (returns false during hydration),
// no setState-in-effect. Flags come from localStorage, so we render nothing
// server-side to avoid a hydration mismatch.
const noopSubscribe = () => () => {};

/**
 * Learn-by-doing activation strip: find → see why → save → write. Dismissible,
 * auto-hides when complete.
 */
export function ActivationChecklist() {
  const saved = useSavedList();
  const mounted = useSyncExternalStore(noopSubscribe, () => true, () => false);
  const [flags, setFlags] = useState<OnboardingFlags>(readFlags);

  if (!mounted || flags.dismissed) return null;

  const hasNote = saved.some((g) => g.note.trim().length > 0);
  const { steps, allDone } = checklistSteps(flags, { savedCount: saved.length, hasNote });
  if (allDone) return null;

  return (
    <div
      style={{
        border: "1px solid var(--line)",
        background: "var(--surface-1)",
        padding: "12px 16px",
        marginBottom: 18,
        display: "flex",
        alignItems: "center",
        gap: 16,
        flexWrap: "wrap",
      }}
    >
      <span className="t-xs w-strong" style={{ color: "var(--ink)" }}>Getting started</span>
      <div style={{ display: "flex", gap: 16, flexWrap: "wrap", flex: 1 }}>
        {steps.map((s) => (
          <span
            key={s.id}
            className="t-xs"
            style={{ display: "inline-flex", alignItems: "center", gap: 6, color: s.done ? "var(--muted)" : "var(--ink)" }}
          >
            <span
              aria-hidden
              style={{ width: 14, height: 14, border: "1px solid var(--ink)", display: "inline-flex", alignItems: "center", justifyContent: "center", background: s.done ? "var(--ink)" : "transparent", color: "var(--bg)" }}
            >
              {s.done && <IconCheck size={11} stroke={3} />}
            </span>
            {s.label}
          </span>
        ))}
      </div>
      <button
        type="button"
        onClick={() => {
          dismissChecklist();
          setFlags(readFlags());
        }}
        aria-label="hide checklist"
        style={{ background: "transparent", border: "none", cursor: "pointer", color: "var(--muted)", lineHeight: 0 }}
      >
        <IconX size={15} />
      </button>
    </div>
  );
}
