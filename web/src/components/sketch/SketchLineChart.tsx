"use client";

import { useEffect, useRef, useState } from "react";

/** One ad (or similar) shown inside a point's tooltip — a mini thumbnail
 * when media exists, else a small glyph tile. */
export type ChartTooltipItem = {
  text: string;
  mediaUrl?: string;
  mediaType?: "image" | "video";
};

export type ChartPoint = {
  label: string;
  value: number;
  /** Optional hover detail: a title line plus the exact items (e.g. ads
   * launched that week) as mini thumbnails. `linesLabel` introduces them. */
  tooltip?: { title: string; items?: ChartTooltipItem[]; linesLabel?: string };
};

const INK = "var(--ink)";
const MUTED = "var(--muted)";
const PAPER = "var(--paper)";

/** Max thumbnails shown in a tooltip before collapsing to "+n more". */
const TOOLTIP_MAX_ITEMS = 8;

/** Mini creative preview: image, first video frame, or a glyph tile. */
function TooltipThumb({ item }: { item: ChartTooltipItem }) {
  const frame: React.CSSProperties = {
    width: 38,
    height: 50,
    objectFit: "cover",
    display: "block",
    border: "1px solid var(--line)",
    background: "var(--surface-3)",
  };
  if (item.mediaUrl && item.mediaType === "video") {
    return <video src={item.mediaUrl} muted playsInline preload="metadata" title={item.text} style={frame} />;
  }
  if (item.mediaUrl) {
    // eslint-disable-next-line @next/next/no-img-element -- dynamic /api/media proxy thumbnails; next/image is a poor fit for an internal tool
    return <img src={item.mediaUrl} alt={item.text} loading="lazy" title={item.text} style={frame} />;
  }
  return (
    <span
      aria-label={item.text}
      title={item.text}
      style={{ ...frame, display: "inline-flex", alignItems: "center", justifyContent: "center", color: "var(--faint)" }}
    >
      ▣
    </span>
  );
}

/**
 * A crisp line chart with readable point markers and hover tooltips.
 * Monochrome line on the theme's ink; dots get a paper ring so they read
 * against the line. Hovering snaps to the nearest point and shows its
 * label, value, and any caller-supplied detail lines.
 */
export function SketchLineChart({
  points,
  height = 180,
  highlightLast = false,
  valueLabel = (v: number) => String(v),
  className,
}: {
  points: ChartPoint[];
  height?: number;
  highlightLast?: boolean;
  /** Formats the hovered value line, e.g. (v) => `${v} ads live`. */
  valueLabel?: (v: number) => string;
  className?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(0);
  const [hover, setHover] = useState<number | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const update = () => setW(el.getBoundingClientRect().width);
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const padL = 6;
  const padR = 6;
  const padT = 12;
  const padB = 24;
  const plotW = Math.max(0, w - padL - padR);
  const plotH = Math.max(0, height - padT - padB);

  const values = points.map((p) => p.value);
  const min = Math.min(...values, 0);
  const max = Math.max(...values, 1);
  const range = max - min || 1;

  const xs = points.map((_, i) =>
    points.length <= 1 ? padL + plotW / 2 : padL + (i / (points.length - 1)) * plotW,
  );
  const ys = points.map((p) => padT + plotH - ((p.value - min) / range) * plotH);
  const poly = xs.map((x, i) => `${x},${ys[i]}`).join(" ");

  // Thin the x labels so they never collide: cap at ~7, always keep the ends.
  const labelStep = Math.max(1, Math.ceil(points.length / 7));
  const showLabel = (i: number) => i === 0 || i === points.length - 1 || i % labelStep === 0;

  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    if (!xs.length) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    let best = 0;
    for (let i = 1; i < xs.length; i++) if (Math.abs(xs[i] - mx) < Math.abs(xs[best] - mx)) best = i;
    setHover(best);
  };

  const h = hover !== null && hover < points.length ? hover : null;
  const tip = h !== null ? points[h] : null;
  const items = tip?.tooltip?.items ?? [];
  const shownItems = items.slice(0, TOOLTIP_MAX_ITEMS);
  const hiddenCount = items.length - shownItems.length;
  // Keep the tooltip on the canvas: flip sides past the midpoint.
  const tipLeft = h !== null ? xs[h] : 0;
  const tipFlip = h !== null && w > 0 && xs[h] > w * 0.55;

  return (
    <div ref={ref} className={className} style={{ width: "100%", height, position: "relative" }}>
      {w > 0 && (
        <svg width={w} height={height} style={{ display: "block" }} onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
          {/* baseline + left axis */}
          <line x1={padL} y1={padT} x2={padL} y2={padT + plotH} stroke={MUTED} strokeWidth={1} />
          <line x1={padL} y1={padT + plotH} x2={padL + plotW} y2={padT + plotH} stroke={MUTED} strokeWidth={1} />
          {/* hover guide */}
          {h !== null && <line x1={xs[h]} y1={padT} x2={xs[h]} y2={padT + plotH} stroke={MUTED} strokeWidth={1} strokeDasharray="3 3" />}
          {/* the line */}
          <polyline points={poly} fill="none" stroke={INK} strokeWidth={2.5} strokeLinejoin="round" strokeLinecap="round" />
          {/* point markers — paper ring so each dot reads against the line */}
          {xs.map((x, i) => {
            const big = (i === xs.length - 1 && highlightLast) || i === h;
            return <circle key={i} cx={x} cy={ys[i]} r={big ? 6 : 4} fill={INK} stroke={PAPER} strokeWidth={1.5} />;
          })}
        </svg>
      )}

      {/* x labels, positioned under their dots */}
      <div aria-hidden style={{ position: "absolute", bottom: 2, left: 0, right: 0, height: 16 }}>
        {points.map((p, i) =>
          showLabel(i) ? (
            <span
              key={i}
              className="font-mono nums"
              style={{
                position: "absolute",
                left: Math.min(Math.max(xs[i] ?? 0, 14), Math.max(w - 14, 14)),
                transform: "translateX(-50%)",
                fontSize: "0.65rem",
                color: i === h ? "var(--ink)" : "var(--muted)",
                whiteSpace: "nowrap",
              }}
            >
              {p.label}
            </span>
          ) : null,
        )}
      </div>

      {/* tooltip */}
      {tip && (
        <div
          role="status"
          style={{
            position: "absolute",
            top: 0,
            left: tipFlip ? undefined : Math.min(tipLeft + 12, Math.max(w - 210, 0)),
            right: tipFlip ? Math.min(w - tipLeft + 12, Math.max(w - 210, 0)) : undefined,
            maxWidth: 210,
            background: "var(--surface-4)",
            border: "1px solid var(--ink)",
            boxShadow: "3px 3px 0 var(--ink)",
            padding: "8px 10px",
            pointerEvents: "none",
            zIndex: 5,
          }}
        >
          <div className="t-xs w-strong nums">{tip.tooltip?.title ?? tip.label}</div>
          <div className="t-xs nums" style={{ color: "var(--ink-soft)", marginTop: 2 }}>{valueLabel(tip.value)}</div>
          {shownItems.length > 0 && (
            <div style={{ marginTop: 6 }}>
              {tip.tooltip?.linesLabel && (
                <div className="t-xs w-strong" style={{ color: "var(--ink-soft)", marginBottom: 4 }}>{tip.tooltip.linesLabel}</div>
              )}
              <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                {shownItems.map((item, i) => (
                  <TooltipThumb key={i} item={item} />
                ))}
              </div>
              {hiddenCount > 0 && (
                <div className="t-xs" style={{ color: "var(--faint)", marginTop: 4 }}>+{hiddenCount} more</div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
