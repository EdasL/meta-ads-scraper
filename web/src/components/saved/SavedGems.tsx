"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { IconArrowLeft } from "@tabler/icons-react";
import {
  applyGroupIdRemaps,
  countByFolder,
  getGroupIdRemapLog,
  memberOfFolder,
  sortGroups,
  type SavedGroup,
} from "@/lib/saved";
import { createGroup, deleteGroup, renameGroup, useLikedGems, useSavedGroups, useSavedList } from "@/lib/useSaved";
import { useSavedAdsList } from "@/lib/useSavedAds";
import { EmptyState } from "@/components/ui";
import { FolderMenu } from "./FolderMenu";
import { FolderTiles } from "./FolderTiles";
import { SavedAdCard } from "./SavedAdCard";
import { LikedRow, SavedCard } from "./SavedCard";

const page: React.CSSProperties = { maxWidth: 1100, margin: "0 auto", padding: "24px 22px 80px" };
const selectStyle: React.CSSProperties = {
  background: "var(--surface-3)",
  border: "1px solid var(--line)",
  color: "var(--ink)",
  fontFamily: "var(--font-mono), monospace",
  fontSize: "var(--fs-xs)",
  padding: "5px 8px",
};

/** The open collection: a folder id, a built-in sentinel (Liked, Ads), or none. */
type ActiveView = number | "liked" | "ads" | null;

function folderFromLocation(): ActiveView {
  if (typeof window === "undefined") return null;
  const raw = new URLSearchParams(window.location.search).get("folder");
  if (!raw) return null;
  if (raw === "liked") return "liked";
  if (raw === "ads") return "ads";
  const id = Number(raw);
  return Number.isFinite(id) && id !== 0 ? id : null;
}

/** Inline-renameable folder heading for the folder view. */
function FolderHeading({
  folder,
  editing,
  onEditStart,
  onEditEnd,
}: {
  folder: SavedGroup;
  editing: boolean;
  onEditStart: () => void;
  onEditEnd: () => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);

  const commit = () => {
    const name = (inputRef.current?.value ?? "").trim();
    if (name && name !== folder.name) renameGroup(folder.id, name);
    onEditEnd();
  };

  if (editing) {
    return (
      <input
        ref={inputRef}
        defaultValue={folder.name}
        maxLength={60}
        autoFocus
        onFocus={(e) => e.currentTarget.select()}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") commit();
          if (e.key === "Escape") onEditEnd();
        }}
        aria-label="Folder name"
        className="t-2xl w-heavy"
        style={{
          background: "var(--surface-3)",
          border: "1px solid var(--line)",
          color: "var(--ink)",
          padding: "0 6px",
          minWidth: 0,
          maxWidth: "100%",
        }}
      />
    );
  }
  return (
    <button
      type="button"
      onClick={onEditStart}
      title="Rename"
      aria-label={`Rename ${folder.name}`}
      className="t-2xl w-heavy"
      style={{
        background: "transparent",
        border: "none",
        color: "var(--ink)",
        padding: 0,
        cursor: "text",
        textAlign: "left",
        minWidth: 0,
        overflow: "hidden",
        textOverflow: "ellipsis",
        whiteSpace: "nowrap",
      }}
    >
      {folder.name}
    </button>
  );
}

export function SavedGems() {
  const saved = useSavedList();
  const folders = sortGroups(useSavedGroups());
  const [tag, setTag] = useState("");
  const liked = useLikedGems();
  const savedAds = useSavedAdsList();
  const [active, setActive] = useState<ActiveView>(folderFromLocation);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [remapSeq, setRemapSeq] = useState(0);
  const [prevIdsKey, setPrevIdsKey] = useState("");
  // Tile DOM keys survive the temp→real id swap; editing and the open folder
  // both follow their own temp id (exact attribution via the store's log).
  const [stableKeys, setStableKeys] = useState<ReadonlyMap<number, number>>(new Map());

  // Adjust-during-render, pass 1: apply new remap-log entries exactly.
  const remapLog = getGroupIdRemapLog();
  const appliedEdit = applyGroupIdRemaps(remapLog, remapSeq, stableKeys, editingId);
  const appliedActive = applyGroupIdRemaps(remapLog, remapSeq, new Map(), typeof active === "number" ? active : null);
  if (appliedEdit.lastSeq !== remapSeq) {
    setStableKeys(appliedEdit.stableKeys);
    if (appliedEdit.editingId !== editingId) setEditingId(appliedEdit.editingId);
    if (typeof active === "number" && appliedActive.editingId !== active) setActive(appliedActive.editingId);
    setRemapSeq(appliedEdit.lastSeq);
  }
  // Pass 2: a post-remap id that no longer exists means its folder was deleted
  // or wiped by a hydrate — stop editing / back to the top level.
  const idsKey = folders.map((f) => f.id).join(",");
  if (idsKey !== prevIdsKey) {
    if (appliedEdit.editingId != null && !folders.some((f) => f.id === appliedEdit.editingId)) {
      setEditingId(null);
    }
    if (typeof active === "number" && appliedActive.editingId != null && !folders.some((f) => f.id === appliedActive.editingId)) {
      setActive(null);
    }
    setPrevIdsKey(idsKey);
  }

  // ?folder=<id> rides history.replaceState (this Next's router.replace is a
  // no-op with searchParams). The effect also remaps the URL when a temp id
  // reconciles, since `active` changes then too.
  useEffect(() => {
    const url = new URL(window.location.href);
    if (active == null) url.searchParams.delete("folder");
    else url.searchParams.set("folder", String(active));
    window.history.replaceState(window.history.state, "", url);
  }, [active]);

  const tags = useMemo(() => Array.from(new Set(saved.flatMap((g) => g.tags))).sort(), [saved]);
  const counts = useMemo(() => countByFolder(saved), [saved]);

  // Unknown/foreign ?folder id (or pre-hydration): the top level renders.
  const openFolder = typeof active === "number" ? folders.find((f) => f.id === active) : undefined;
  const likedOpen = active === "liked";
  const adsOpen = active === "ads";

  const visible = openFolder ? memberOfFolder(saved, openFolder.id) : saved;
  const filtered = tag ? visible.filter((g) => g.tags.includes(tag)) : visible;
  const savedById = useMemo(() => new Map(saved.map((g) => [g.advertiserId, g])), [saved]);

  const addFolder = () => {
    const tempId = createGroup("New folder");
    if (tempId == null) return;
    setActive(tempId);
    setEditingId(tempId);
  };

  const confirmDelete = (folder: SavedGroup) => {
    const count = counts.get(folder.id) ?? 0;
    if (count > 0 && !window.confirm(`Delete "${folder.name}"? Gems stay in your saved list.`)) return;
    deleteGroup(folder.id);
    if (active === folder.id) setActive(null);
  };

  // The full empty state only when there is truly nothing to show — likes,
  // folders and saved ads keep the page alive even with zero saves.
  if (saved.length === 0 && liked.length === 0 && folders.length === 0 && savedAds.length === 0) {
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

  const tagFilter = tags.length > 0 && (
    <label className="eyebrow" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
      Tag
      <select value={tag} onChange={(e) => setTag(e.target.value)} style={selectStyle}>
        <option value="">All</option>
        {tags.map((t) => (
          <option key={t} value={t}>#{t}</option>
        ))}
      </select>
    </label>
  );

  // Saved ads carry no tags, so the tag filter has nothing to act on here.
  if (adsOpen) {
    return (
      <main style={page}>
        <button
          type="button"
          onClick={() => setActive(null)}
          className="eyebrow"
          style={{ display: "inline-flex", alignItems: "center", gap: 6, background: "transparent", border: "none", color: "var(--muted)", padding: 0, cursor: "pointer", marginBottom: 10 }}
        >
          <IconArrowLeft size={14} aria-hidden />
          All gems
        </button>
        <header style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 18 }}>
          <h1 className="t-2xl w-heavy" style={{ margin: 0 }}>Ads</h1>
          <span className="nums t-sm" style={{ color: "var(--muted)" }}>{savedAds.length}</span>
        </header>
        {savedAds.length === 0 ? (
          <p className="t-sm" style={{ color: "var(--muted)", marginTop: 8 }}>
            No saved ads yet. Save one with the heart on a gem&apos;s ads.
          </p>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(230px, 1fr))", gap: 12 }}>
            {savedAds.map((a) => (
              <SavedAdCard key={a.external_ad_id} ad={a} />
            ))}
          </div>
        )}
      </main>
    );
  }

  if (likedOpen) {
    // Saved members honor the tag filter; liked-but-unsaved rows carry no
    // tags, so an active tag filter hides them entirely (disclosed choice).
    const rows = liked
      .map((item) => ({ item, gem: savedById.get(item.advertiserId) }))
      .filter(({ gem }) => (tag ? gem?.tags.includes(tag) : true));
    return (
      <main style={page}>
        <button
          type="button"
          onClick={() => setActive(null)}
          className="eyebrow"
          style={{ display: "inline-flex", alignItems: "center", gap: 6, background: "transparent", border: "none", color: "var(--muted)", padding: 0, cursor: "pointer", marginBottom: 10 }}
        >
          <IconArrowLeft size={14} aria-hidden />
          All gems
        </button>
        <header style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 18 }}>
          <h1 className="t-2xl w-heavy" style={{ margin: 0 }}>Liked</h1>
          <span className="nums t-sm" style={{ color: "var(--muted)" }}>{liked.length}</span>
          <span style={{ marginLeft: "auto" }}>{tagFilter}</span>
        </header>
        {rows.length === 0 ? (
          <p className="t-sm" style={{ color: "var(--muted)", marginTop: 8 }}>
            {tag ? "Nothing matches the current filters." : "Nothing liked yet."}
          </p>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(230px, 1fr))", gap: 12 }}>
            {rows.map(({ item, gem }) => (
              <LikedRow key={item.advertiserId} item={item} gem={gem} />
            ))}
          </div>
        )}
      </main>
    );
  }

  if (openFolder) {
    return (
      <main style={page}>
        <button
          type="button"
          onClick={() => setActive(null)}
          className="eyebrow"
          style={{ display: "inline-flex", alignItems: "center", gap: 6, background: "transparent", border: "none", color: "var(--muted)", padding: 0, cursor: "pointer", marginBottom: 10 }}
        >
          <IconArrowLeft size={14} aria-hidden />
          All gems
        </button>
        <header style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 18 }}>
          <FolderHeading
            key={stableKeys.get(openFolder.id) ?? openFolder.id}
            folder={openFolder}
            editing={editingId === openFolder.id}
            onEditStart={() => setEditingId(openFolder.id)}
            onEditEnd={() => setEditingId(null)}
          />
          <span className="nums t-sm" style={{ color: "var(--muted)" }}>{counts.get(openFolder.id) ?? 0}</span>
          <FolderMenu onDelete={() => confirmDelete(openFolder)} />
          <span style={{ marginLeft: "auto" }}>{tagFilter}</span>
        </header>
        {filtered.length === 0 ? (
          <p className="t-sm" style={{ color: "var(--muted)", marginTop: 8 }}>
            {tag ? "Nothing matches the current filters." : "No gems in this folder yet."}
          </p>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(230px, 1fr))", gap: 12 }}>
            {filtered.map((g) => (
              <SavedCard key={g.advertiserId} gem={g} />
            ))}
          </div>
        )}
      </main>
    );
  }

  return (
    <main style={page}>
      <header style={{ marginBottom: 18 }}>
        <h1 className="t-2xl w-heavy" style={{ margin: 0 }}>My gems</h1>
        <p className="t-sm" style={{ color: "var(--muted)", marginTop: 6 }}>{saved.length} saved.</p>
      </header>

      <div className="eyebrow" style={{ marginBottom: 8 }}>Folders</div>
      <FolderTiles
        folders={folders}
        counts={counts}
        likedCount={liked.length}
        adsCount={savedAds.length}
        keyFor={(id) => stableKeys.get(id) ?? id}
        onOpen={setActive}
        onOpenLiked={() => setActive("liked")}
        onOpenAds={() => setActive("ads")}
        onCreate={addFolder}
      />

      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, marginBottom: 8 }}>
        <div className="eyebrow">All gems</div>
        {tagFilter}
      </div>
      {filtered.length === 0 ? (
        <p className="t-sm" style={{ color: "var(--muted)", marginTop: 8 }}>
          {saved.length === 0 ? "No gems saved yet." : "Nothing matches the current filters."}
        </p>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(230px, 1fr))", gap: 12 }}>
          {filtered.map((g) => (
            <SavedCard key={g.advertiserId} gem={g} />
          ))}
        </div>
      )}
    </main>
  );
}
