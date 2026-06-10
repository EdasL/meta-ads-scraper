"use client";

import { useState } from "react";

/** Initials for the fallback tile: first letters of the first two words. */
function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  return (
    words
      .slice(0, 2)
      .map((w) => w[0].toUpperCase())
      .join("") || "?"
  );
}

/**
 * Small app icon for list items: the store icon when `src` is present, else an
 * initials tile from the app/page name. The tile (and a broken image) degrade
 * to initials, never to an empty box. Sits next to the visible name, so the
 * fallback is aria-hidden and the image alt stays plain.
 */
export function AppIcon({ src, name, size = 28 }: { src?: string; name: string; size?: number }) {
  const [broken, setBroken] = useState(false);
  const frame: React.CSSProperties = {
    width: size,
    height: size,
    flexShrink: 0,
    borderRadius: 6,
    border: "1px solid var(--line)",
    background: "var(--surface-3)",
    overflow: "hidden",
  };
  if (src && !broken) {
    return (
      // eslint-disable-next-line @next/next/no-img-element -- external store CDN icons; next/image is a poor fit for an internal tool
      <img
        src={src}
        alt="app icon"
        loading="lazy"
        onError={() => setBroken(true)}
        style={{ ...frame, display: "block", objectFit: "cover" }}
      />
    );
  }
  return (
    <span
      aria-hidden
      className="font-mono"
      style={{
        ...frame,
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        color: "var(--ink-soft)",
        fontSize: "var(--fs-xs)",
      }}
    >
      {initials(name)}
    </span>
  );
}
