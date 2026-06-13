"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import type { ShortlistRow, ShortlistSort, SortDir } from "@/types/contracts";
import { growthLine, monthLabel } from "@/lib/why";
import { compactCount } from "@/lib/format";
import { SORT_DEFAULT_DIR } from "@/lib/query";
import { AppIcon, SaveToggle, ScoreBadge, Trend } from "@/components/ui";

/**
 * The Discover comparison table: one gem per row, the numbers that matter
 * side by side so candidates can be compared at a glance. Each metric column
 * pairs the current level with its recent growth — level says how big,
 * growth says how fast. Headers whose metric the server can order by are
 * click-to-sort: first click orders the key's natural way, second reverses
 * it, third clears back to the default order. Clicking a row opens the
 * dossier; `onOpen` fires first so the feed can remember its position.
 */
export function GemTable({
  rows,
  sort,
  dir,
  onSort,
  onOpen,
}: {
  rows: ShortlistRow[];
  sort: ShortlistSort;
  dir: SortDir;
  onSort: (sort: ShortlistSort, dir: SortDir) => void;
  onOpen?: (row: ShortlistRow) => void;
}) {
  return (
    <div style={{ overflowX: "auto" }}>
      <table className="gem-table">
        <thead>
          <tr>
            <th>App</th>
            <th>Cohort</th>
            <SortHeader id="gemscore" label="GemScore" sort={sort} dir={dir} onSort={onSort} align="right" />
            <SortHeader id="fastest" label="Ads" sort={sort} dir={dir} onSort={onSort} align="right" />
            <th className="num" title="Play Store installs and the change over the last 28 days">
              Installs
            </th>
            <th className="num" title="Store reviews and the change over the last 28 days">
              Reviews
            </th>
            <SortHeader id="newest" label="First ad" sort={sort} dir={dir} onSort={onSort} />
            <th aria-label="Save" />
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <GemRow key={row.advertiser_id} row={row} onOpen={onOpen} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SortHeader({
  id,
  label,
  sort,
  dir,
  onSort,
  align,
}: {
  id: ShortlistSort;
  label: string;
  sort: ShortlistSort;
  dir: SortDir;
  onSort: (sort: ShortlistSort, dir: SortDir) => void;
  align?: "right";
}) {
  const active = sort === id;
  // Natural = the key's own meaning (best score / fastest / newest first),
  // shown as ▾; reversed shows ▴.
  const natural = active && dir === SORT_DEFAULT_DIR[id];

  // Click cycle: natural → reversed → back to the default feed order.
  function cycle() {
    if (!active) onSort(id, SORT_DEFAULT_DIR[id]);
    else if (natural) onSort(id, dir === "desc" ? "asc" : "desc");
    else onSort("gemscore", SORT_DEFAULT_DIR.gemscore);
  }

  return (
    <th
      className={align === "right" ? "num" : undefined}
      aria-sort={active ? (natural ? "descending" : "ascending") : undefined}
    >
      <button type="button" className={`gem-table-sort${active ? " is-active" : ""}`} onClick={cycle}>
        {label}
        {active ? (natural ? " ▾" : " ▴") : ""}
      </button>
    </th>
  );
}

function GemRow({ row, onOpen }: { row: ShortlistRow; onOpen?: (row: ShortlistRow) => void }) {
  const router = useRouter();
  const name = row.app_name || row.page_name;
  const href = `/gems/${row.advertiser_id}`;
  const trend = growthLine(row.growth, { active_ad_count: row.active_ad_count, first_ad_at: row.first_ad_at });

  // The whole row is a target, but anchors and buttons inside it (name link,
  // save toggle) keep their own behavior.
  function openFromRow(e: React.MouseEvent) {
    if ((e.target as HTMLElement).closest("a, button")) return;
    onOpen?.(row);
    router.push(href);
  }

  return (
    <tr onClick={openFromRow}>
      <td>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
          <AppIcon src={row.icon_url} name={name} size={26} />
          <span style={{ display: "inline-flex", flexDirection: "column", gap: 1, minWidth: 0 }}>
            <Link href={href} className="w-strong" style={{ color: "inherit", textDecoration: "none" }} onClick={() => onOpen?.(row)}>
              {name}
            </Link>
            {row.tagline && (
              <span className="t-xs gem-table-tagline" title={row.tagline}>
                {row.tagline}
              </span>
            )}
          </span>
        </span>
      </td>
      <td className="t-xs" style={{ color: "var(--muted)" }}>
        {row.cohort}
      </td>
      <td className="num">
        <ScoreBadge score={row.gem_score} variant="card" />
      </td>
      <td className="num">
        <MetricCell
          level={`${row.active_ad_count} active`}
          growth={<Trend glyph={trend.glyph} label={trend.text} tone={trend.tone} />}
        />
      </td>
      <td className="num">
        <MetricCell level={row.installs != null ? compactCount(row.installs) : null} growth={deltaLine(row.installs_delta_28d)} />
      </td>
      <td className="num">
        <MetricCell level={row.rating_count != null ? compactCount(row.rating_count) : null} growth={deltaLine(row.rating_count_delta_28d)} />
      </td>
      <td className="t-xs nums" style={{ whiteSpace: "nowrap" }}>
        {monthLabel(row.first_ad_at) ?? "—"}
      </td>
      <td onClick={(e) => e.stopPropagation()}>
        <SaveToggle row={row} size="sm" />
      </td>
    </tr>
  );
}

/** Level on top, recent growth underneath. Either line may be missing —
 * unknown renders as a dash, never as zero. */
function MetricCell({ level, growth }: { level: React.ReactNode | null; growth: React.ReactNode | null }) {
  return (
    <span style={{ display: "inline-flex", flexDirection: "column", alignItems: "flex-end", gap: 2 }}>
      <span className="nums">{level ?? "—"}</span>
      {growth != null && (
        <span className="t-xs" style={{ color: "var(--muted)", whiteSpace: "nowrap" }}>
          {growth}
        </span>
      )}
    </span>
  );
}

/** "+1.2K in 28d" from a raw delta; null when the snapshot span is missing.
 * A real 0 delta still renders — flat is information. */
function deltaLine(delta: number | undefined): React.ReactNode | null {
  if (delta == null) return null;
  const sign = delta > 0 ? "+" : "";
  return `${sign}${compactCount(delta)} in 28d`;
}
