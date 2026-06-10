"use client";

import type { ShortlistRow } from "@/types/contracts";
import { STAGES, VERDICTS, type SavedVerdict, type Stage } from "@/lib/saved";
import { addTag, allTags, removeTag, setNote, setStage, setVerdict, useSavedGem } from "@/lib/useSaved";
import { NoteEditor, SaveToggle, TagInput } from "@/components/ui";

const selectStyle: React.CSSProperties = {
  background: "var(--surface-3)",
  border: "1px solid var(--line)",
  color: "var(--ink)",
  fontFamily: "var(--font-mono), monospace",
  fontSize: "var(--fs-xs)",
  padding: "5px 8px",
  outline: "none",
};

/**
 * "Your gem" panel. Not saved → one-line prompt + watch. Saved → note, tags,
 * stage, and what-you-do verdict. Capture in one tap, structure on demand.
 */
export function GemNotes({ row }: { row: ShortlistRow }) {
  const saved = useSavedGem(row.advertiser_id);

  if (!saved) {
    return (
      <section style={{ border: "1px dashed var(--line)", padding: 18, textAlign: "center", display: "flex", flexDirection: "column", alignItems: "center", gap: 12 }}>
        <p className="t-sm" style={{ color: "var(--muted)", margin: 0 }}>Save it to keep notes and tags.</p>
        <SaveToggle row={row} />
      </section>
    );
  }

  const suggestions = allTags();
  return (
    <section style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10 }}>
        <h2 className="t-md w-strong" style={{ margin: 0 }}>Your notes</h2>
        <SaveToggle row={row} size="sm" />
      </div>

      <NoteEditor key={saved.advertiserId} value={saved.note} onSave={(v) => setNote(saved.advertiserId, v)} />

      <TagInput
        tags={saved.tags}
        suggestions={suggestions}
        onAdd={(t) => addTag(saved.advertiserId, t)}
        onRemove={(t) => removeTag(saved.advertiserId, t)}
      />

      <div style={{ display: "flex", gap: 18, flexWrap: "wrap" }}>
        <label className="eyebrow" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          Stage
          <select value={saved.stage} onChange={(e) => setStage(saved.advertiserId, e.target.value as Stage)} style={selectStyle}>
            {STAGES.map((s) => (
              <option key={s.id} value={s.id}>{s.label}</option>
            ))}
          </select>
        </label>
        <label className="eyebrow" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          Verdict
          <select
            value={saved.verdict ?? ""}
            onChange={(e) => setVerdict(saved.advertiserId, (e.target.value || undefined) as SavedVerdict | undefined)}
            style={selectStyle}
          >
            <option value="">—</option>
            {VERDICTS.map((v) => (
              <option key={v.id} value={v.id}>{v.label}</option>
            ))}
          </select>
        </label>
      </div>
    </section>
  );
}
