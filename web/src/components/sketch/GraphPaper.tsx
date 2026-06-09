"use client";

import type { CSSProperties, ReactNode } from "react";

/** A flat panel (was graph paper). Clean white surface, no grid. */
export function GraphPaper({
  children,
  className,
  style,
}: {
  children?: ReactNode;
  className?: string;
  style?: CSSProperties;
  cell?: number;
}) {
  return (
    <div
      className={className}
      style={{ backgroundColor: "var(--paper)", position: "relative", ...style }}
    >
      {children}
    </div>
  );
}
