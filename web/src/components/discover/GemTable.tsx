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
            <th title="The quiz or landing page this app's ads drive traffic to before the store. Click to open it.">
              Quiz
            </th>
            <SortHeader id="gemscore" label="GemScore" sort={sort} dir={dir} onSort={onSort} align="right" />
            <SortHeader id="fastest" label="Ads" sort={sort} dir={dir} onSort={onSort} align="right" />
            <th className="num" title="Traction: Play installs for Android apps, store reviews for iOS (Apple never exposes installs) — with the change over the last 28 days">
              Traction
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

  // Winners = ads still live after 30+ days (winner_depth, the core GemScore v3
  // signal). When the app has proven winners, the ad-count cell leads with them
  // and keeps the growth direction as a glyph; early apps with no long-runners
  // fall back to the plain growth line so "rising" still shows.
  const winners = row.longrun_ad_count ?? 0;
  const adsGrowth =
    winners > 0 ? (
      <Trend glyph={trend.glyph} label={`${winners} run 30d+`} tone={trend.tone} />
    ) : (
      <Trend glyph={trend.glyph} label={trend.text} tone={trend.tone} />
    );

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
      <td>
        <FunnelCell url={row.funnel_url} />
      </td>
      <td className="num">
        <ScoreBadge score={row.gem_score} variant="card" />
      </td>
      <td className="num">
        <MetricCell level={`${row.active_ad_count} active`} growth={adsGrowth} />
      </td>
      <td className="num">
        <TractionCell row={row} />
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

/** The quiz/landing page the app's ads drive to (funnel_url, matview migration
 * 125). When captured, link straight to it; the click is isolated from the row's
 * navigate-to-dossier handler. Most app ads go straight to the store, so this is
 * blank for the majority — a populated cell means a real web2app funnel. */
function FunnelCell({ url }: { url?: string }) {
  if (!url) return <span className="t-xs" style={{ color: "var(--muted)" }}>—</span>;
  let label = url;
  try {
    label = new URL(url).hostname.replace(/^www\./, "");
  } catch {
    /* keep the raw string if it isn't a parseable URL */
  }
  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      onClick={(e) => e.stopPropagation()}
      className="t-xs"
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 3,
        maxWidth: 160,
        color: "var(--accent-ink, var(--link, var(--ink)))",
        textDecoration: "underline",
        whiteSpace: "nowrap",
        overflow: "hidden",
        textOverflow: "ellipsis",
      }}
      title={url}
    >
      {label}
    </a>
  );
}

/** Adaptive traction: Play exposes installs (near-exact), Apple never does, so
 * iOS rows fall back to review count. The per-row signal label keeps a mixed
 * iOS/Android feed honest — you always know which number you're reading. When
 * store is unknown, prefer whichever absolute is present. */
function TractionCell({ row }: { row: ShortlistRow }) {
  const useInstalls =
    row.store === "android" || (row.store == null && row.installs != null);
  const value = useInstalls ? row.installs : row.rating_count;
  const delta = useInstalls ? row.installs_delta_28d : row.rating_count_delta_28d;
  const signal = useInstalls ? "installs" : "reviews";
  return (
    <span style={{ display: "inline-flex", flexDirection: "column", alignItems: "flex-end", gap: 2 }}>
      <span className="nums">{value != null ? compactCount(value) : "—"}</span>
      {value != null && (
        <span className="t-xs" style={{ color: "var(--muted)", whiteSpace: "nowrap" }}>
          {signal}
          {deltaLine(delta) != null ? ` · ${deltaLine(delta)}` : ""}
        </span>
      )}
    </span>
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
