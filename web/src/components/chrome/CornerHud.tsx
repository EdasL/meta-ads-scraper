"use client";

import type { ReactNode } from "react";

/**
 * NRTHVIEW-style technical chrome pinned to the four corners of a relative
 * container. Non-interactive overlay (pointerEvents none). Pass any node per
 * corner; these read like a build/session readout (rev, coords, take, clock).
 */
export function CornerHud({
  topLeft,
  topRight,
  bottomLeft,
  bottomRight,
  inset = 14,
}: {
  topLeft?: ReactNode;
  topRight?: ReactNode;
  bottomLeft?: ReactNode;
  bottomRight?: ReactNode;
  inset?: number;
}) {
  const base = { position: "absolute" as const, pointerEvents: "none" as const, zIndex: 40 };
  return (
    <>
      {topLeft && <div className="hud" style={{ ...base, top: inset, left: inset }}>{topLeft}</div>}
      {topRight && <div className="hud" style={{ ...base, top: inset, right: inset, textAlign: "right" }}>{topRight}</div>}
      {bottomLeft && <div className="hud" style={{ ...base, bottom: inset, left: inset }}>{bottomLeft}</div>}
      {bottomRight && <div className="hud" style={{ ...base, bottom: inset, right: inset, textAlign: "right" }}>{bottomRight}</div>}
    </>
  );
}
