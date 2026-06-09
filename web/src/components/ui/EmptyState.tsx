"use client";

import type { ReactNode } from "react";
import { IconMoodEmpty } from "@tabler/icons-react";
import { Button, ButtonLink } from "./Button";

/**
 * Empty / no-results state = exactly three elements: one visual, one status
 * line, one CTA (spec §4). Never a wall of text.
 */
export function EmptyState({
  title,
  hint,
  ctaLabel,
  ctaHref,
  onCta,
  art,
}: {
  title: string;
  hint?: string;
  ctaLabel?: string;
  ctaHref?: string;
  onCta?: () => void;
  art?: ReactNode;
}) {
  return (
    <div
      style={{
        maxWidth: 420,
        margin: "56px auto",
        textAlign: "center",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 16,
        padding: "0 24px",
      }}
    >
      <div aria-hidden style={{ color: "var(--faint)", lineHeight: 0 }}>
        {art ?? <IconMoodEmpty size={56} stroke={1.25} />}
      </div>
      <div className="t-lg w-strong">{title}</div>
      {hint && <p className="t-sm measure" style={{ color: "var(--muted)" }}>{hint}</p>}
      {ctaLabel &&
        (ctaHref ? (
          <ButtonLink href={ctaHref}>{ctaLabel}</ButtonLink>
        ) : (
          <Button onClick={onCta}>{ctaLabel}</Button>
        ))}
    </div>
  );
}
