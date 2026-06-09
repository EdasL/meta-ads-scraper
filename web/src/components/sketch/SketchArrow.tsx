"use client";

import { INK } from "./palette";

type Pt = [number, number];

/**
 * Clean straight-line connector through points (sharp, no wobble).
 * Optional arrowhead at the final point. Monochrome.
 */
export function SketchConnector({
  width,
  height,
  points,
  color = INK,
  strokeWidth = 2,
  arrow = false,
  dashed = false,
  className,
  style,
}: {
  width: number;
  height: number;
  points: Pt[];
  color?: string;
  strokeWidth?: number;
  roughness?: number;
  arrow?: boolean;
  sag?: number;
  dashed?: boolean;
  className?: string;
  style?: React.CSSProperties;
}) {
  if (points.length < 2) return null;
  const poly = points.map((p) => p.join(",")).join(" ");

  let head: Pt[] = [];
  if (arrow) {
    const [px, py] = points[points.length - 2];
    const [qx, qy] = points[points.length - 1];
    const ang = Math.atan2(qy - py, qx - px);
    const len = 11;
    const spread = 0.42;
    head = [
      [qx - len * Math.cos(ang - spread), qy - len * Math.sin(ang - spread)],
      [qx, qy],
      [qx - len * Math.cos(ang + spread), qy - len * Math.sin(ang + spread)],
    ];
  }

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      aria-hidden
      className={className}
      style={{ overflow: "visible", pointerEvents: "none", ...style }}
    >
      <polyline
        points={poly}
        fill="none"
        stroke={color}
        strokeWidth={strokeWidth}
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeDasharray={dashed ? "6 5" : undefined}
      />
      {arrow && (
        <polyline points={head.map((p) => p.join(",")).join(" ")} fill="none" stroke={color} strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round" />
      )}
    </svg>
  );
}

export function SketchArrow(props: Omit<Parameters<typeof SketchConnector>[0], "arrow">) {
  return <SketchConnector {...props} arrow />;
}

export function RedString(props: Parameters<typeof SketchConnector>[0]) {
  return <SketchConnector {...props} color={props.color ?? INK} />;
}
