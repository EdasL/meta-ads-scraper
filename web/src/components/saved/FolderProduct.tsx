"use client";

import { useState } from "react";
import { Button, ButtonLink } from "@/components/ui";
import type { SavedGroup } from "@/lib/saved";

/** A folder's reference product is separate from its saved competitor cards. */
export function FolderProduct({ folder, onSave }: {
  folder: SavedGroup;
  onSave: (id: number, product: string) => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(folder.product ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const edit = () => { setDraft(folder.product ?? ""); setError(""); setEditing(true); };
  const save = async () => {
    if (saving) return;
    setSaving(true);
    setError("");
    try {
      await onSave(folder.id, draft.trim());
      setEditing(false);
    } catch {
      setError("Could not save the product. Please try again.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div style={{ marginBottom: 22, paddingBottom: 18, borderBottom: "1px solid var(--line)" }}>
      {editing ? (
        <form onSubmit={(e) => { e.preventDefault(); void save(); }} style={{ maxWidth: 650 }}>
          <label htmlFor={`folder-product-${folder.id}`} className="t-sm w-strong">Product you’re researching</label>
          <p className="t-xs" style={{ color: "var(--muted)", margin: "5px 0 8px" }}>Add its name, website, or a short description. Saved brands in this folder will be references for it.</p>
          <textarea
            id={`folder-product-${folder.id}`}
            autoFocus
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            maxLength={800}
            rows={2}
            disabled={saving}
            placeholder="Product name or website, and what it helps people do"
            style={{ display: "block", width: "100%", boxSizing: "border-box", resize: "vertical", padding: "10px 12px", background: "var(--surface-2)", color: "var(--ink)", border: "1px solid var(--line)", font: "inherit" }}
          />
          {error && <p role="alert" className="t-xs" style={{ color: "var(--down)" }}>{error}</p>}
          <div style={{ display: "flex", gap: 10, marginTop: 10 }}>
            <Button type="submit" disabled={saving}>{saving ? "Saving…" : "Save product"}</Button>
            <Button variant="ghost" disabled={saving} onClick={() => setEditing(false)}>Cancel</Button>
          </div>
        </form>
      ) : folder.product ? (
        <div style={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: 12 }}>
          <div style={{ flex: "1 1 240px", minWidth: 0 }}>
            <div className="eyebrow" style={{ color: "var(--muted)", marginBottom: 5 }}>Product</div>
            <div className="t-sm" style={{ overflowWrap: "anywhere", whiteSpace: "pre-wrap" }}>{folder.product}</div>
          </div>
          <Button variant="ghost" onClick={edit}>Edit product</Button>
          <ButtonLink href={`/hunt?folder=${folder.id}`}>Hunt for this product</ButtonLink>
        </div>
      ) : (
        <div style={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: 12 }}>
          <Button variant="ghost" onClick={edit} disabled={folder.id <= 0}>Attach product</Button>
          <span className="t-xs" style={{ color: "var(--muted)" }}>Which product are these saved brands for?</span>
        </div>
      )}
    </div>
  );
}
