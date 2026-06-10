"use client";

import { useState } from "react";
import { IconChevronDown, IconChevronRight } from "@tabler/icons-react";
import { growthLine, pushPull, trueAdCount } from "@/lib/why";
import { SignalChips } from "@/components/ui";
import type { ShortlistRow } from "@/types/contracts";

/**
 * The one place grug still speaks. A plain disclosure (reused from GemVerdict:
 * useState + aria-expanded + chevron + a borderTop reveal) whose toggle label is
 * grug ("grug explain?"). Open shows the LLM explainer's three fields
 * (what it do / what problem / why it work) plus a quantified "why it's a gem"
 * breakdown built from numbers alone — so it degrades gracefully when the
 * explainer hasn't been generated yet.
 *
 * Used on the Discover card (expand-in-place, inside a Link → the toggle must
 * stopPropagation/preventDefault) and on the detail page (replacing the old
 * inline "how grug know?").
 */
function GrugField({ label, value }: { label: string; value: string }) {
  if (!value) return null;
  return (
    <div>
      <div className="eyebrow" style={{ color: "var(--ink)", marginBottom: 4 }}>{label}</div>
      <p className="t-sm" style={{ margin: 0, color: "var(--ink-soft)" }}>{value}</p>
    </div>
  );
}

function Stat({ value, label }: { value: string | number; label: string }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <span className="t-md w-heavy nums" style={{ lineHeight: 1 }}>{value}</span>
      <span className="font-mono" style={{ fontSize: "var(--fs-xs)", color: "var(--muted)" }}>{label}</span>
    </div>
  );
}

export function GrugExplainer({
  row,
  distinctCreatives,
  onOpen,
}: {
  row: ShortlistRow;
  distinctCreatives?: number;
  onOpen?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const { explainer, growth } = row;
  const { push } = pushPull(row);
  const g = growthLine(growth, row);
  // Detail-page dossiers carry the true ad-library count; cards fall back to
  // the matview count (trueAdCount handles both).
  const ads = trueAdCount(row);

  return (
    <div>
      <button
        type="button"
        onClick={(e) => {
          // The card is a Link — keep the click from navigating.
          e.stopPropagation();
          e.preventDefault();
          setOpen((o) => {
            if (!o) onOpen?.();
            return !o;
          });
        }}
        aria-expanded={open}
        className="font-mono caps"
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 5,
          background: "transparent",
          border: "none",
          cursor: "pointer",
          color: "var(--muted)",
          fontSize: "var(--fs-xs)",
          letterSpacing: "0.08em",
          padding: 0,
        }}
      >
        {open ? <IconChevronDown size={14} /> : <IconChevronRight size={14} />}
        {open ? "hide" : "grug explain?"}
      </button>

      {open && (
        <div style={{ marginTop: 12, paddingTop: 12, borderTop: "1px solid var(--line)", display: "flex", flexDirection: "column", gap: 16 }}>
          {explainer ? (
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              <GrugField label="what it do" value={explainer.what_it_does} />
              <GrugField label="what problem" value={explainer.problem_solved} />
              <GrugField label="why it work" value={explainer.why_working} />
            </div>
          ) : (
            <p className="t-sm" style={{ margin: 0, color: "var(--muted)" }}>
              grug still write words for this one. numbers below say why grug like it.
            </p>
          )}

          <div>
            <div className="eyebrow" style={{ color: "var(--ink)", marginBottom: 8 }}>why it&apos;s a gem</div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: "14px 24px", marginBottom: 12 }}>
              <Stat value={g.text} label="growth" />
              <Stat value={`${ads.count}${ads.capped ? "+" : ""}`} label="ads live" />
              <Stat value={`${row.max_longevity_days}d`} label="longest run" />
              {distinctCreatives != null && distinctCreatives > 0 && (
                <Stat value={distinctCreatives} label="distinct ads" />
              )}
            </div>
            {push.length > 0 && <SignalChips chips={push} compact />}
          </div>
        </div>
      )}
    </div>
  );
}
