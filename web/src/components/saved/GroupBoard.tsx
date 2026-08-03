"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { IconChevronLeft, IconChevronRight, IconDots } from "@tabler/icons-react";
import {
  applyGroupIdRemaps,
  bucketByGroup,
  getGroupIdRemapLog,
  sortGroups,
  type SavedGem,
  type SavedGroup,
} from "@/lib/saved";
import {
  createGroup,
  deleteGroup,
  listGroups,
  moveGroupLeft,
  moveGroupRight,
  renameGroup,
  setGroup,
  useSavedGroups,
  useSavedList,
} from "@/lib/useSaved";

const moveBtn: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  background: "transparent",
  border: "1px solid var(--line)",
  color: "var(--ink)",
  cursor: "pointer",
  padding: "2px 6px",
  lineHeight: 0,
};

const selectStyle: React.CSSProperties = {
  background: "var(--surface-3)",
  border: "1px solid var(--line)",
  color: "var(--ink)",
  fontFamily: "var(--font-mono), monospace",
  fontSize: "var(--fs-xs)",
  padding: "2px 4px",
  maxWidth: 96,
};

/** Column ⋯ menu. Fixed-position so the board's horizontal scroll container
 * can't clip it; any scroll or resize closes it (the anchor moves away). */
function ColumnMenu({
  canMoveLeft,
  canMoveRight,
  canDelete,
  onMoveLeft,
  onMoveRight,
  onDelete,
}: {
  canMoveLeft: boolean;
  canMoveRight: boolean;
  canDelete: boolean;
  onMoveLeft: () => void;
  onMoveRight: () => void;
  onDelete: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const btnRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  const enabledItems = () =>
    Array.from(menuRef.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]:not(:disabled)') ?? []);

  // Menu-pattern keys: arrows wrap across enabled items, Home/End jump.
  // Escape lives on the document listener below; Tab keeps browser behavior.
  const onMenuKey = (e: React.KeyboardEvent) => {
    const items = enabledItems();
    if (items.length === 0) return;
    const idx = items.indexOf(document.activeElement as HTMLButtonElement);
    let next: number | null = null;
    if (e.key === "ArrowDown") next = idx < 0 ? 0 : (idx + 1) % items.length;
    else if (e.key === "ArrowUp") next = idx < 0 ? items.length - 1 : (idx - 1 + items.length) % items.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = items.length - 1;
    if (next != null) {
      e.preventDefault();
      items[next]?.focus();
    }
  };

  useEffect(() => {
    if (!open) return;
    const close = () => setOpen(false);
    const onDown = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) close();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        close();
        btnRef.current?.focus();
      }
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    // Capture-phase scroll: the board's own scroll container fires no window
    // scroll event, and a moved anchor would strand the fixed panel.
    window.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("resize", close);
    };
  }, [open]);

  const toggle = () => {
    if (open) {
      setOpen(false);
      return;
    }
    const rect = btnRef.current?.getBoundingClientRect();
    if (!rect) return;
    const width = 168;
    setPos({
      top: rect.bottom + 4,
      left: Math.min(rect.left, window.innerWidth - width - 8),
    });
    setOpen(true);
    // Focus the first enabled item once the panel exists (Dropdown's rAF idiom).
    requestAnimationFrame(() => enabledItems()[0]?.focus());
  };

  const item = (label: string, onClick: () => void, disabled: boolean) => (
    <button
      type="button"
      role="menuitem"
      disabled={disabled}
      onClick={() => {
        setOpen(false);
        onClick();
      }}
      className="font-mono"
      style={{
        display: "block",
        width: "100%",
        textAlign: "left",
        background: "transparent",
        border: "none",
        color: "var(--ink)",
        fontSize: "var(--fs-xs)",
        padding: "7px 9px",
        cursor: disabled ? "default" : "pointer",
        opacity: disabled ? 0.3 : 1,
      }}
    >
      {label}
    </button>
  );

  return (
    <div ref={wrapRef} style={{ display: "inline-flex" }}>
      <button
        ref={btnRef}
        type="button"
        aria-label="Group options"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={toggle}
        className="gb-press"
        style={moveBtn}
      >
        <IconDots size={14} />
      </button>
      {open && pos && (
        <div
          ref={menuRef}
          role="menu"
          className="dd-panel"
          onKeyDown={onMenuKey}
          style={{
            position: "fixed",
            top: pos.top,
            left: pos.left,
            width: 168,
            zIndex: 50,
            padding: 4,
            background: "var(--surface-4)",
            border: "1.5px solid var(--ink)",
            boxShadow: "var(--shadow-hard)",
          }}
        >
          {item("Move left", onMoveLeft, !canMoveLeft)}
          {item("Move right", onMoveRight, !canMoveRight)}
          {item("Delete group", onDelete, !canDelete)}
        </div>
      )}
    </div>
  );
}

/** Column header: click the name to rename inline; Enter/blur commits, Esc
 * cancels, empty reverts. */
function ColumnHeader({
  group,
  count,
  editing,
  onEditStart,
  onEditEnd,
  canMoveLeft,
  canMoveRight,
  canDelete,
  onDelete,
}: {
  group: SavedGroup;
  count: number;
  editing: boolean;
  onEditStart: () => void;
  onEditEnd: () => void;
  canMoveLeft: boolean;
  canMoveRight: boolean;
  canDelete: boolean;
  onDelete: () => void;
}) {
  // Uncontrolled: the input mounts fresh each time editing opens, so
  // defaultValue + autoFocus cover init without effect-driven state.
  const inputRef = useRef<HTMLInputElement>(null);

  const commit = () => {
    const name = (inputRef.current?.value ?? "").trim();
    if (name && name !== group.name) renameGroup(group.id, name);
    onEditEnd();
  };

  return (
    <div
      className="eyebrow"
      style={{
        display: "flex",
        alignItems: "center",
        gap: 6,
        borderBottom: "1px solid var(--line)",
        paddingBottom: 6,
      }}
    >
      {editing ? (
        <input
          ref={inputRef}
          defaultValue={group.name}
          maxLength={60}
          autoFocus
          onFocus={(e) => e.currentTarget.select()}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === "Enter") commit();
            if (e.key === "Escape") onEditEnd();
          }}
          aria-label="Group name"
          className="eyebrow"
          style={{
            flex: 1,
            minWidth: 0,
            background: "var(--surface-3)",
            border: "1px solid var(--line)",
            color: "var(--ink)",
            padding: "1px 4px",
          }}
        />
      ) : (
        <button
          type="button"
          onClick={onEditStart}
          aria-label={`Rename ${group.name}`}
          title="Rename"
          className="eyebrow"
          style={{
            flex: 1,
            minWidth: 0,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            textAlign: "left",
            background: "transparent",
            border: "none",
            color: "var(--ink)",
            padding: 0,
            cursor: "text",
          }}
        >
          {group.name}
        </button>
      )}
      <span className="nums" style={{ flexShrink: 0 }}>{count}</span>
      <ColumnMenu
        canMoveLeft={canMoveLeft}
        canMoveRight={canMoveRight}
        canDelete={canDelete}
        onMoveLeft={() => moveGroupLeft(group.id)}
        onMoveRight={() => moveGroupRight(group.id)}
        onDelete={onDelete}
      />
    </div>
  );
}

function GemCard({ gem, groups, groupIdx }: { gem: SavedGem; groups: SavedGroup[]; groupIdx: number }) {
  const atStart = groupIdx === 0;
  const atEnd = groupIdx === groups.length - 1;
  const others = groups.filter((_, i) => i !== groupIdx);

  return (
    <div
      style={{
        border: "1.5px solid var(--ink)",
        background: "var(--paper)",
        padding: 10,
        display: "flex",
        flexDirection: "column",
        gap: 8,
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "baseline" }}>
        <Link
          href={`/gems/${gem.advertiserId}`}
          className="t-sm w-strong"
          style={{
            color: "var(--ink)",
            textDecoration: "none",
            flex: 1,
            minWidth: 0,
            display: "-webkit-box",
            WebkitBoxOrient: "vertical",
            WebkitLineClamp: 2,
            overflow: "hidden",
            wordBreak: "break-word",
          }}
        >
          {gem.appName ?? gem.pageName}
        </Link>
        <span className="t-md w-heavy nums" style={{ flexShrink: 0 }}>
          {Math.round(gem.scoreSnapshot.gemScore)}
        </span>
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 6 }}>
        <button
          type="button"
          onClick={() => setGroup(gem.advertiserId, groups[groupIdx - 1].id)}
          aria-label="Move to the group on the left"
          disabled={atStart}
          className="gb-press"
          style={{ ...moveBtn, opacity: atStart ? 0.3 : 1 }}
        >
          <IconChevronLeft size={14} />
        </button>
        {groups.length > 2 && (
          <select
            value=""
            onChange={(e) => {
              const id = Number(e.target.value);
              if (id) setGroup(gem.advertiserId, id);
            }}
            aria-label="Move to group"
            style={selectStyle}
          >
            <option value="">Move to</option>
            {others.map((g) => (
              <option key={g.id} value={g.id}>{g.name}</option>
            ))}
          </select>
        )}
        <button
          type="button"
          onClick={() => setGroup(gem.advertiserId, groups[groupIdx + 1].id)}
          aria-label="Move to the group on the right"
          disabled={atEnd}
          className="gb-press"
          style={{ ...moveBtn, opacity: atEnd ? 0.3 : 1 }}
        >
          <IconChevronRight size={14} />
        </button>
      </div>
    </div>
  );
}

/** Board over the saved gems: one column per user group. `gems` may be
 * tag-filtered by the page; counts and delete semantics always use the full
 * saved list, so a filter can never hide cards from a delete decision. */
export function GroupBoard({ gems }: { gems: SavedGem[] }) {
  const groups = sortGroups(useSavedGroups());
  const allSaved = useSavedList();
  const [editingId, setEditingId] = useState<number | null>(null);
  const [prevIdsKey, setPrevIdsKey] = useState("");
  const boardRef = useRef<HTMLDivElement>(null);
  // Stable DOM keys: a column created this session keeps its temp key after
  // the create response swaps in the real id, so the rename input (and the
  // typist's focus + draft) survives reconciliation. State, not a ref — it is
  // read during render (React key) and only ever updated alongside editingId
  // in the adjust-during-render block below.
  const [stableKeys, setStableKeys] = useState<ReadonlyMap<number, number>>(new Map());

  // Cursor into the store's temp→real remap log (see getGroupIdRemapLog):
  // exact attribution, applied at most once per entry via the seq cursor.
  const [remapSeq, setRemapSeq] = useState(0);

  // Adjust-during-render (react.dev "adjusting state when props change").
  // Pass 1 — apply any new remap-log entries exactly: the real id inherits its
  // OWN temp column's DOM key and editing session, no array-order inference,
  // so two same-batch creates can never cross-contaminate.
  const remapLog = getGroupIdRemapLog();
  const applied = applyGroupIdRemaps(remapLog, remapSeq, stableKeys, editingId);
  if (applied.lastSeq !== remapSeq) {
    setStableKeys(applied.stableKeys);
    if (applied.editingId !== editingId) setEditingId(applied.editingId);
    setRemapSeq(applied.lastSeq);
  }
  // Pass 2 — an editing id (post-remap) that no longer exists in groups means
  // its column was deleted or wiped by a hydrate: stop editing.
  const idsKey = groups.map((g) => g.id).join(",");
  if (idsKey !== prevIdsKey) {
    if (applied.editingId != null && !groups.some((g) => g.id === applied.editingId)) {
      setEditingId(null);
    }
    setPrevIdsKey(idsKey);
  }

  const buckets = bucketByGroup(gems, groups);
  // Unfiltered membership: drives the count badge and the delete confirm.
  const trueBuckets = bucketByGroup(allSaved, groups);

  const addGroup = () => {
    const before = new Set(groups.map((g) => g.id));
    createGroup("New group");
    // listGroups reads the store synchronously; the optimistic row is there.
    const created = sortGroups(listGroups()).find((g) => !before.has(g.id));
    if (created) setEditingId(created.id);
    requestAnimationFrame(() => {
      const el = boardRef.current;
      if (!el) return;
      const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      el.scrollTo({ left: el.scrollWidth, behavior: still ? "auto" : "smooth" });
    });
  };

  const confirmDelete = (group: SavedGroup) => {
    const target = groups.find((g) => g.id !== group.id);
    if (!target) return;
    const count = trueBuckets.get(group.id)?.length ?? 0;
    if (count > 0 && !window.confirm(`Delete "${group.name}"? Its cards move to "${target.name}".`)) return;
    deleteGroup(group.id);
  };

  if (groups.length === 0) return null; // pre-hydration; the store fills in

  return (
    <div
      ref={boardRef}
      style={{
        display: "grid",
        gridTemplateColumns: `repeat(${groups.length + 1}, minmax(200px, 280px))`,
        gap: 14,
        overflowX: "auto",
        alignItems: "start",
        paddingBottom: 8,
      }}
    >
      <style>{`
        .gb-press { transition: transform 0.15s ease-out; }
        .gb-press:active { transform: scale(0.97); }
        @media (prefers-reduced-motion: reduce) {
          .gb-press { transition: none; }
          .gb-press:active { transform: none; }
        }
      `}</style>
      {groups.map((col, idx) => {
        const items = buckets.get(col.id) ?? [];
        const trueCount = trueBuckets.get(col.id)?.length ?? 0;
        return (
          <div key={stableKeys.get(col.id) ?? col.id} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <ColumnHeader
              group={col}
              count={trueCount}
              editing={editingId === col.id}
              onEditStart={() => setEditingId(col.id)}
              onEditEnd={() => setEditingId(null)}
              canMoveLeft={idx > 0}
              canMoveRight={idx < groups.length - 1}
              canDelete={groups.length > 1}
              onDelete={() => confirmDelete(col)}
            />
            {items.map((g) => (
              <GemCard key={g.advertiserId} gem={g} groups={groups} groupIdx={idx} />
            ))}
            {items.length === 0 && (
              <div className="t-xs" style={{ color: "var(--muted)" }}>
                {trueCount > 0 ? "None match the tag filter." : "No gems here yet."}
              </div>
            )}
          </div>
        );
      })}
      <div style={{ paddingTop: 2 }}>
        <button
          type="button"
          onClick={addGroup}
          className="t-sm w-strong gb-press"
          style={{
            background: "transparent",
            border: "1.5px solid var(--ink)",
            color: "var(--ink)",
            padding: "10px 14px",
            cursor: "pointer",
            whiteSpace: "nowrap",
          }}
        >
          New group
        </button>
      </div>
    </div>
  );
}
