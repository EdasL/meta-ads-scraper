"use client";

import { useState, type KeyboardEvent } from "react";
import { IconX } from "@tabler/icons-react";
import { Button } from "@/components/ui";
import { editTerms, formatSettings } from "@/lib/hunt";
import type { HuntProposal } from "@/lib/api";

/** Mirror of the server's competitor-app cap (hunt_controller.go). */
const MAX_COMPETITORS = 3;

/**
 * The editable side of a Peon proposal: term chips (× to remove, an input to
 * add) and competitor checkboxes (all checked by default), plus the Start hunt
 * button. Renders ONLY under the latest assistant message that carries a
 * proposal and has not yet had a round started from it (that gate lives in
 * HuntView).
 *
 * `editTerms` keeps the term list in lockstep with the server's normalization,
 * so what the founder sees is exactly what the round will run. Start hunt is
 * disabled while the term list is empty, a round is already active, or a submit
 * is in flight.
 */
export function ProposalCard({
  proposal,
  roundActive,
  onStart,
}: {
  proposal: HuntProposal;
  roundActive: boolean;
  onStart: (terms: string[], competitorApps: string[]) => Promise<void>;
}) {
  const [terms, setTerms] = useState<string[]>(() => editTerms(proposal.terms, {}));
  // Server caps a round at MAX_COMPETITORS competitor apps (422 beyond it), so
  // default-check at most that many and block a further check past the cap.
  const [checked, setChecked] = useState<Set<string>>(() => new Set(proposal.competitor_apps.slice(0, MAX_COMPETITORS)));
  const [draft, setDraft] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function addDraft() {
    const next = editTerms(terms, { add: draft });
    setTerms(next);
    setDraft("");
  }

  function onDraftKey(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      addDraft();
    } else if (e.key === "Backspace" && draft === "" && terms.length) {
      setTerms(editTerms(terms, { remove: terms[terms.length - 1] }));
    }
  }

  function toggleCompetitor(app: string) {
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(app)) next.delete(app);
      else if (next.size < MAX_COMPETITORS) next.add(app);
      return next;
    });
  }

  async function start() {
    if (submitting || roundActive || terms.length === 0) return;
    setSubmitting(true);
    setError(null);
    try {
      await onStart(terms, proposal.competitor_apps.filter((a) => checked.has(a)));
    } catch {
      setError("Could not start the hunt. Try again.");
      setSubmitting(false);
    }
  }

  const disabled = submitting || roundActive || terms.length === 0;
  // Read-only: Peon sets scrape knobs; the founder tunes them via chat, not here.
  const settingsLabel = formatSettings(proposal.settings);

  return (
    <div
      style={{
        border: "1.5px solid var(--ink)",
        background: "var(--surface-1)",
        boxShadow: "var(--shadow-hard)",
        padding: "16px 18px",
        marginTop: 10,
        display: "flex",
        flexDirection: "column",
        gap: 14,
      }}
    >
      <div>
        <div className="eyebrow" style={{ marginBottom: 8 }}>
          Search terms
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, alignItems: "center" }}>
          {terms.map((t) => (
            <span
              key={t}
              className="font-mono"
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 4,
                fontSize: "var(--fs-xs)",
                border: "1px solid var(--ink)",
                padding: "2px 4px 2px 8px",
              }}
            >
              {t}
              <button
                type="button"
                aria-label={`remove ${t}`}
                onClick={() => setTerms(editTerms(terms, { remove: t }))}
                style={{ background: "transparent", border: "none", cursor: "pointer", color: "var(--muted)", lineHeight: 0, padding: 0 }}
              >
                <IconX size={12} />
              </button>
            </span>
          ))}
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onDraftKey}
            onBlur={addDraft}
            placeholder="add a term"
            className="font-mono"
            style={{
              flex: "1 1 120px",
              minWidth: 120,
              background: "transparent",
              border: "none",
              borderBottom: "1px solid var(--line)",
              color: "var(--ink)",
              fontSize: "var(--fs-xs)",
              padding: "4px 2px",
              outline: "none",
            }}
          />
        </div>
      </div>

      {proposal.competitor_apps.length > 0 && (
        <div>
          <div className="eyebrow" style={{ marginBottom: 8 }}>
            Competitor apps · up to {MAX_COMPETITORS}
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {proposal.competitor_apps.map((app) => {
              const isChecked = checked.has(app);
              const atCap = !isChecked && checked.size >= MAX_COMPETITORS;
              return (
                <label
                  key={app}
                  className="t-sm"
                  style={{ display: "inline-flex", alignItems: "center", gap: 8, cursor: atCap ? "not-allowed" : "pointer", color: atCap ? "var(--muted)" : "var(--ink)" }}
                >
                  <input
                    type="checkbox"
                    checked={isChecked}
                    disabled={atCap}
                    onChange={() => toggleCompetitor(app)}
                    style={{ accentColor: "var(--ink)" }}
                  />
                  {app}
                </label>
              );
            })}
          </div>
        </div>
      )}

      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <Button onClick={start} disabled={disabled}>
          Start hunt
        </Button>
        {settingsLabel && (
          <span className="t-xs font-mono" style={{ color: "var(--muted)" }}>
            {settingsLabel}
          </span>
        )}
        {roundActive && !error && (
          <span className="t-xs" style={{ color: "var(--muted)" }}>
            A round is already running.
          </span>
        )}
        {error && (
          <span className="t-xs" style={{ color: "var(--down)" }}>
            {error}
          </span>
        )}
      </div>
    </div>
  );
}
