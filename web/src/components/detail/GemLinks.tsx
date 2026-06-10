"use client";

import { IconExternalLink } from "@tabler/icons-react";
import { buildGemLinks } from "@/lib/links";
import type { GemDossier } from "@/types/contracts";

/**
 * The "where to go look" links — replaces the old "where clicks go" funnel.
 * Renders ONLY the links whose data exists, each a real external anchor styled
 * as a tappable chip. The destination info the funnel used to imply now lives
 * here as one-click links the founder can actually follow. Link construction is
 * the pure `buildGemLinks` in `lib/links.ts` (unit-tested).
 */
const chipStyle: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 7,
  padding: "9px 14px",
  border: "1.5px solid var(--ink)",
  background: "var(--paper)",
  color: "var(--ink)",
  textDecoration: "none",
};

export function GemLinks({ gem }: { gem: GemDossier }) {
  const links = buildGemLinks(gem);
  if (links.length === 0) return null;
  return (
    <section>
      <h2 className="eyebrow" style={{ margin: "0 0 12px", color: "var(--ink)" }}>go look</h2>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 10 }}>
        {links.map((l) => (
          <a
            key={l.label}
            href={l.href}
            target="_blank"
            rel="noopener noreferrer"
            className="font-mono caps"
            style={chipStyle}
          >
            {l.label}
            <IconExternalLink size={14} stroke={2} />
          </a>
        ))}
      </div>
    </section>
  );
}
