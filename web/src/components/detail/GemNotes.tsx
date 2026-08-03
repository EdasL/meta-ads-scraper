"use client";

import type { ShortlistRow } from "@/types/contracts";
import { VERDICTS, type SavedVerdict } from "@/lib/saved";
import { addTag, allTags, removeTag, setNote, setVerdict, useSavedGem } from "@/lib/useSaved";
import { NoteEditor, TagInput } from "@/components/ui";
import { FolderPicker } from "@/components/saved/FolderPicker";

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
  const suggestions = saved ? allTags() : [];

  // ONE FolderPicker element for both states: the section, the header row,
  // and the picker's child position never change across the save transition,
  // so React's index-based reconciliation keeps the SAME component instance
  // (same type, same position, only props change) — the picker a heart press
  // just opened survives the unsaved→saved branch swap instead of remounting
  // closed. Only the sibling text and the section chrome vary.
  return (
    <section
      style={
        saved
          ? { display: "flex", flexDirection: "column", gap: 14 }
          : { border: "1px dashed var(--line)", padding: 18, display: "flex", flexDirection: "column", gap: 12 }
      }
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10 }}>
        {saved ? (
          <h2 className="t-md w-strong" style={{ margin: 0 }}>Your notes</h2>
        ) : (
          <p className="t-sm" style={{ color: "var(--muted)", margin: 0 }}>Save it to keep notes and tags.</p>
        )}
        <FolderPicker advertiserId={row.advertiser_id} row={row} variant="heart" size={saved ? "sm" : "md"} />
      </div>

      {saved && (<>
      <NoteEditor key={saved.advertiserId} value={saved.note} onSave={(v) => setNote(saved.advertiserId, v)} />

      <TagInput
        tags={saved.tags}
        suggestions={suggestions}
        onAdd={(t) => addTag(saved.advertiserId, t)}
        onRemove={(t) => removeTag(saved.advertiserId, t)}
      />

      <div style={{ display: "flex", gap: 18, flexWrap: "wrap" }}>
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
      </>)}
    </section>
  );
}
