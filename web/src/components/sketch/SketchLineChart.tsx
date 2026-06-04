"use client";

import { useEffect, useRef, useState } from "react";
import { INK, MUTED } from "./palette";

export type ChartPoint = { label: string; value: number };

/** A crisp, sharp line chart (no hand-drawn wobble). Strict B&W. */
export function SketchLineChart({
  points,
  height = 180,
  highlightLast = false,
  className,
}: {
  points: ChartPoint[];
  height?: number;
  highlightLast?: boolean;
  className?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(0);

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
  const padT = 10;
  const padB = 22;
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

  return (
    <div ref={ref} className={className} style={{ width: "100%", height, position: "relative" }}>
      {w > 0 && (
        <svg width={w} height={height} style={{ display: "block" }}>
          {/* baseline + left axis */}
          <line x1={padL} y1={padT} x2={padL} y2={padT + plotH} stroke={MUTED} strokeWidth={1} />
          <line x1={padL} y1={padT + plotH} x2={padL + plotW} y2={padT + plotH} stroke={MUTED} strokeWidth={1} />
          {/* the line */}
          <polyline points={poly} fill="none" stroke={INK} strokeWidth={2.5} strokeLinejoin="round" strokeLinecap="round" />
          {/* point markers */}
          {xs.map((x, i) => {
            const last = i === xs.length - 1 && highlightLast;
            return <circle key={i} cx={x} cy={ys[i]} r={last ? 5.5 : 3.5} fill={INK} />;
          })}
        </svg>
      )}
      <div
        className="font-mono"
        style={{
          position: "absolute",
          bottom: 2,
          left: padL,
          right: padR,
          display: "flex",
          justifyContent: "space-between",
          fontSize: "0.58rem",
          color: "var(--muted)",
        }}
      >
        {points.map((p, i) => (
          <span key={i}>{p.label}</span>
        ))}
      </div>
    </div>
  );
}
