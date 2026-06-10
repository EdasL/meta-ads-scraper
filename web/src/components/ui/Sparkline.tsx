"use client";

import { useEffect, useRef, useState } from "react";

// Inline SVG resolves CSS vars, so the line follows the active theme.
const INK = "var(--ink)";
const MUTED = "var(--muted)";

/**
 * Tiny monochrome line — the hockey-stick is the pitch. No axes or labels.
 * Width is measured from the container if not given. Highlights the last point.
 */
export function Sparkline({
  values,
  width,
  height = 34,
  strokeWidth = 2,
}: {
  values: number[];
  width?: number;
  height?: number;
  strokeWidth?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [measured, setMeasured] = useState(0);

  useEffect(() => {
    if (width) return;
    const el = ref.current;
    if (!el) return;
    const update = () => setMeasured(el.getBoundingClientRect().width);
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, [width]);

  const W = width ?? measured;
  const vals = values.length >= 2 ? values : [...values, ...values, 0].slice(0, 2);
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const range = max - min || 1;
  const n = vals.length;
  const padY = strokeWidth + 1;

  const pts = vals.map((v, i) => {
    const x = n <= 1 ? W / 2 : 1 + (i / (n - 1)) * (W - 2);
    const y = height - padY - ((v - min) / range) * (height - padY * 2);
    return [x, y] as const;
  });
  const poly = pts.map((p) => p.join(",")).join(" ");
  const last = pts[pts.length - 1];

  return (
    <div ref={ref} style={{ width: width ? `${width}px` : "100%", height }}>
      {W > 0 && (
        <svg width={W} height={height} style={{ display: "block" }} aria-hidden>
          <polyline points={poly} fill="none" stroke={INK} strokeWidth={strokeWidth} strokeLinejoin="round" strokeLinecap="round" />
          {last && <circle cx={last[0]} cy={last[1]} r={strokeWidth + 1} fill={INK} />}
          {pts.length > 0 && (
            <circle cx={pts[0][0]} cy={pts[0][1]} r={strokeWidth - 0.5} fill={MUTED} />
          )}
        </svg>
      )}
    </div>
  );
}
