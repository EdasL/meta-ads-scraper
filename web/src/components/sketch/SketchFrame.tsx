"use client";

import type { CSSProperties, ReactNode } from "react";
import { INK, PAPER } from "./palette";

/**
 * A clean, sharp bordered box (NRTHVIEW direction — no hand-drawn wobble).
 * Keeps the original prop surface so existing screens compile unchanged;
 * sketch-only props (roughness, fillStyle, seedKey…) are accepted and ignored.
 */
export function SketchFrame({
  children,
  className,
  style,
  stroke = INK,
  strokeWidth = 1.5,
  fill,
  radius = 0,
  onClick,
}: {
  children?: ReactNode;
  className?: string;
  style?: CSSProperties;
  stroke?: string;
  strokeWidth?: number;
  roughness?: number;
  fill?: string;
  fillStyle?: string;
  fillWeight?: number;
  hachureGap?: number;
  seedKey?: string;
  pad?: number;
  radius?: number;
  onClick?: () => void;
}) {
  return (
    <div
      className={className}
      onClick={onClick}
      style={{
        position: "relative",
        border: `${strokeWidth}px solid ${stroke}`,
        background: fill ?? "transparent",
        borderRadius: radius,
        ...style,
      }}
    >
      {children}
    </div>
  );
}

export { PAPER };
