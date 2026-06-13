"use client";

import type { FunnelRow, FunnelSort, SortDir } from "@/types/contracts";
import { compactCount, isoDateLabel } from "@/lib/format";
import { FUNNEL_SORT_DEFAULT_DIR } from "@/lib/query";

/**
 * The Funnels comparison table: one web-to-app funnel per row, grouped by its
 * landing domain. Because many advertiser pages can drive the same destination,
 * the row is the destination — `advertiser_count` is how many distinct pages
 * point at it, and times-seen/last-seen are summed across them. Headers the
 * server can order by are click-to-sort: first click orders the key's natural
 * way, second reverses it, third clears back to the default (newest). The
 * domain links out to the landing page itself.
 */
export function FunnelTable({
  rows,
  sort,
  dir,
  onSort,
}: {
  rows: FunnelRow[];
  sort: FunnelSort;
  dir: SortDir;
  onSort: (sort: FunnelSort, dir: SortDir) => void;
}) {
  return (
    <div style={{ overflowX: "auto" }}>
      <table className="gem-table">
        <thead>
          <tr>
            <th>Landing page</th>
            <th>Offer</th>
            <SortHeader id="advertisers" label="Advertisers" sort={sort} dir={dir} onSort={onSort} align="right" />
            <SortHeader id="seen" label="Times seen" sort={sort} dir={dir} onSort={onSort} align="right" />
            <SortHeader id="newest" label="Last seen" sort={sort} dir={dir} onSort={onSort} />
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <FunnelTableRow key={row.landing_domain} row={row} />
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
  id: FunnelSort;
  label: string;
  sort: FunnelSort;
  dir: SortDir;
  onSort: (sort: FunnelSort, dir: SortDir) => void;
  align?: "right";
}) {
  const active = sort === id;
  const natural = active && dir === FUNNEL_SORT_DEFAULT_DIR[id];

  function cycle() {
    if (!active) onSort(id, FUNNEL_SORT_DEFAULT_DIR[id]);
    else if (natural) onSort(id, dir === "desc" ? "asc" : "desc");
    else onSort("newest", FUNNEL_SORT_DEFAULT_DIR.newest);
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

function FunnelTableRow({ row }: { row: FunnelRow }) {
  const href = row.sample_landing_url ?? `https://${row.landing_domain}`;
  const quizLabel =
    row.has_quiz && row.max_quiz_steps != null ? `Quiz · ${row.max_quiz_steps} steps` : row.has_quiz ? "Quiz" : null;
  const cta = ctaLabel(row.cta_type);

  return (
    <tr>
      <td>
        <span style={{ display: "inline-flex", flexDirection: "column", gap: 1, minWidth: 0 }}>
          <a
            href={href}
            target="_blank"
            rel="noreferrer noopener"
            className="w-strong"
            style={{ color: "inherit", textDecoration: "none" }}
          >
            {row.landing_domain}
          </a>
          {row.sample_page_name && (
            <span className="t-xs gem-table-tagline" title={row.sample_page_name}>
              {row.sample_page_name}
              {row.advertiser_count > 1 ? ` +${row.advertiser_count - 1} more` : ""}
            </span>
          )}
        </span>
      </td>
      <td className="t-xs" style={{ color: "var(--muted)" }}>
        <span style={{ display: "inline-flex", flexDirection: "column", gap: 1 }}>
          {cta && <span>{cta}</span>}
          {quizLabel && <span className="w-strong" style={{ color: "var(--ink)" }}>{quizLabel}</span>}
          {!cta && !quizLabel && "—"}
        </span>
      </td>
      <td className="num nums">{row.advertiser_count}</td>
      <td className="num nums">{compactCount(row.times_seen)}</td>
      <td className="t-xs nums" style={{ whiteSpace: "nowrap" }}>
        {row.last_seen_at ? isoDateLabel(row.last_seen_at) : "—"}
      </td>
    </tr>
  );
}

/** Meta's CTA enum is SCREAMING_SNAKE; show it as a plain label. */
function ctaLabel(cta: string | null): string | null {
  if (!cta) return null;
  const pretty = cta
    .toLowerCase()
    .split("_")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
  return pretty;
}
