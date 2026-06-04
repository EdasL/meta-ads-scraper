"use client";

import type { ReactNode } from "react";
import { INK } from "./palette";

/** A clean framed media tile (was a taped polaroid). Sharp, B&W. */
export function Polaroid({
  caption,
  width = 180,
  children,
}: {
  caption?: string;
  rotate?: number;
  width?: number;
  children?: ReactNode;
}) {
  return (
    <div style={{ width, border: `1.5px solid ${INK}`, background: "var(--paper)" }}>
      <div
        style={{
          aspectRatio: "4 / 5",
          background: "var(--paper-2)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          overflow: "hidden",
          borderBottom: `1.5px solid ${INK}`,
        }}
      >
        {children}
      </div>
      {caption && (
        <div className="font-mono" style={{ fontSize: "0.62rem", padding: "7px 9px", lineHeight: 1.2, textTransform: "uppercase", letterSpacing: "0.04em" }}>
          {caption}
        </div>
      )}
    </div>
  );
}
