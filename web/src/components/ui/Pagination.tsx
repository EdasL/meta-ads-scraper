"use client";

import { pageItems } from "@/lib/pagination";

/**
 * Numbered pager for the Discover grid: ‹ 1 … 4 [5] 6 … 12 › plus an honest
 * "25–48 of 137" range. Hidden entirely when everything fits on one page.
 */
export function Pagination({
  page,
  pageCount,
  total,
  rangeStart,
  rangeEnd,
  onPage,
}: {
  page: number;
  pageCount: number;
  total: number;
  rangeStart: number;
  rangeEnd: number;
  onPage: (page: number) => void;
}) {
  if (pageCount <= 1) return null;

  const box = (active: boolean, disabled = false): React.CSSProperties => ({
    minWidth: 30,
    height: 30,
    padding: "0 7px",
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    border: `1px solid ${active ? "var(--ink)" : "var(--line)"}`,
    background: active ? "var(--ink)" : "transparent",
    color: active ? "var(--bg)" : "var(--ink)",
    fontSize: "var(--fs-xs)",
    cursor: active || disabled ? "default" : "pointer",
    opacity: disabled ? 0.4 : 1,
  });

  return (
    <nav
      aria-label="Pages"
      style={{
        display: "flex",
        flexWrap: "wrap",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 12,
        marginTop: 28,
      }}
    >
      <span className="t-xs nums" style={{ color: "var(--muted)" }}>
        {rangeStart}–{rangeEnd} of {total}
      </span>

      <div className="font-mono nums" style={{ display: "flex", alignItems: "center", gap: 4 }}>
        {/* aria-disabled (not disabled) keeps the button focusable at the
            boundary — a native disabled control silently drops keyboard focus
            to <body> the moment the last/first page is reached. */}
        <button
          type="button"
          aria-label="Previous page"
          aria-disabled={page <= 1}
          onClick={() => page > 1 && onPage(page - 1)}
          style={box(false, page <= 1)}
        >
          ‹
        </button>
        {pageItems(page, pageCount).map((item, i) =>
          item === "gap" ? (
            <span key={`gap-${i}`} aria-hidden style={{ color: "var(--muted)", padding: "0 3px" }}>
              …
            </span>
          ) : (
            <button
              key={item}
              type="button"
              aria-label={`Page ${item}`}
              aria-current={item === page ? "page" : undefined}
              onClick={() => item !== page && onPage(item)}
              style={box(item === page)}
            >
              {item}
            </button>
          ),
        )}
        <button
          type="button"
          aria-label="Next page"
          aria-disabled={page >= pageCount}
          onClick={() => page < pageCount && onPage(page + 1)}
          style={box(false, page >= pageCount)}
        >
          ›
        </button>
      </div>
    </nav>
  );
}
