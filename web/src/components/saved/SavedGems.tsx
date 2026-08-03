"use client";

import { useMemo, useState } from "react";
import {
  applyGroupIdRemaps,
  filterByGroup,
  getGroupIdRemapLog,
  sortGroups,
  type SavedGroup,
} from "@/lib/saved";
import { createGroup, deleteGroup, listGroups, useSavedGroups, useSavedList } from "@/lib/useSaved";
import { EmptyState } from "@/components/ui";
import { FolderChips } from "./FolderChips";
import { SavedCard } from "./SavedCard";

const page: React.CSSProperties = { maxWidth: 1100, margin: "0 auto", padding: "24px 22px 80px" };
const selectStyle: React.CSSProperties = {
  background: "var(--surface-3)",
  border: "1px solid var(--line)",
  color: "var(--ink)",
  fontFamily: "var(--font-mono), monospace",
  fontSize: "var(--fs-xs)",
  padding: "5px 8px",
};

export function SavedGems() {
  const saved = useSavedList();
  const folders = sortGroups(useSavedGroups());
  const [tag, setTag] = useState("");
  const [active, setActive] = useState<number | null>(null);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [remapSeq, setRemapSeq] = useState(0);
  const [prevIdsKey, setPrevIdsKey] = useState("");
  // Chip DOM keys survive the temp→real id swap so a rename input mid-flight
  // keeps its node, focus, and draft (same machinery as the shipped board).
  const [stableKeys, setStableKeys] = useState<ReadonlyMap<number, number>>(new Map());

  // Adjust-during-render, pass 1: apply new remap-log entries exactly — the
  // real id inherits its own temp chip's DOM key, editing and active follow
  // their own temp id only.
  const remapLog = getGroupIdRemapLog();
  const appliedEdit = applyGroupIdRemaps(remapLog, remapSeq, stableKeys, editingId);
  const appliedActive = applyGroupIdRemaps(remapLog, remapSeq, new Map(), active);
  if (appliedEdit.lastSeq !== remapSeq) {
    setStableKeys(appliedEdit.stableKeys);
    if (appliedEdit.editingId !== editingId) setEditingId(appliedEdit.editingId);
    if (appliedActive.editingId !== active) setActive(appliedActive.editingId);
    setRemapSeq(appliedEdit.lastSeq);
  }
  // Pass 2: an id (post-remap) that no longer exists means its folder was
  // deleted or wiped by a hydrate — stop editing / fall back to All.
  const idsKey = folders.map((f) => f.id).join(",");
  if (idsKey !== prevIdsKey) {
    if (appliedEdit.editingId != null && !folders.some((f) => f.id === appliedEdit.editingId)) {
      setEditingId(null);
    }
    if (appliedActive.editingId != null && !folders.some((f) => f.id === appliedActive.editingId)) {
      setActive(null);
    }
    setPrevIdsKey(idsKey);
  }

  const tags = useMemo(() => Array.from(new Set(saved.flatMap((g) => g.tags))).sort(), [saved]);
  const counts = useMemo(() => {
    const m = new Map<number, number>();
    for (const g of saved) {
      if (g.groupId != null) m.set(g.groupId, (m.get(g.groupId) ?? 0) + 1);
    }
    return m;
  }, [saved]);

  // Folder filter first, tag filter on top (AND).
  const inFolder = filterByGroup(saved, active);
  const filtered = tag ? inFolder.filter((g) => g.tags.includes(tag)) : inFolder;

  const addFolder = () => {
    const before = new Set(folders.map((f) => f.id));
    createGroup("New folder");
    // listGroups reads the store synchronously; the optimistic row is there.
    const created = sortGroups(listGroups()).find((f) => !before.has(f.id));
    if (created) {
      setActive(created.id);
      setEditingId(created.id);
    }
  };

  const confirmDelete = (folder: SavedGroup) => {
    const count = counts.get(folder.id) ?? 0;
    if (count > 0 && !window.confirm(`Delete "${folder.name}"? Its gems stay in All.`)) return;
    deleteGroup(folder.id);
    if (active === folder.id) setActive(null);
  };

  if (saved.length === 0) {
    return (
      <main style={page}>
        <h1 className="t-2xl w-heavy" style={{ margin: 0 }}>My gems</h1>
        <EmptyState
          title="No gems saved yet"
          hint="Save a gem from Discover and it shows up here with your notes."
          ctaLabel="Find a gem"
          ctaHref="/discover"
        />
      </main>
    );
  }

  return (
    <main style={page}>
      <header style={{ marginBottom: 18 }}>
        <h1 className="t-2xl w-heavy" style={{ margin: 0 }}>My gems</h1>
        <p className="t-sm" style={{ color: "var(--muted)", marginTop: 6 }}>{saved.length} saved.</p>
      </header>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "flex-start", justifyContent: "space-between" }}>
        <FolderChips
          folders={folders}
          active={active}
          editingId={editingId}
          counts={counts}
          allCount={saved.length}
          keyFor={(id) => stableKeys.get(id) ?? id}
          onSelect={setActive}
          onEditStart={setEditingId}
          onEditEnd={() => setEditingId(null)}
          onDelete={confirmDelete}
          onCreate={addFolder}
        />
        {tags.length > 0 && (
          <label className="eyebrow" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            Tag
            <select value={tag} onChange={(e) => setTag(e.target.value)} style={selectStyle}>
              <option value="">All</option>
              {tags.map((t) => (
                <option key={t} value={t}>#{t}</option>
              ))}
            </select>
          </label>
        )}
      </div>

      {filtered.length === 0 ? (
        <p className="t-sm" style={{ color: "var(--muted)", marginTop: 8 }}>
          {active != null && !tag ? "No gems in this folder yet." : "Nothing matches the current filters."}
        </p>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(230px, 1fr))", gap: 12 }}>
          {filtered.map((g) => (
            <SavedCard key={g.advertiserId} gem={g} folders={folders} />
          ))}
        </div>
      )}
    </main>
  );
}
