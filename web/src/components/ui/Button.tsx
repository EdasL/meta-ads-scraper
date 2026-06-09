"use client";

import Link from "next/link";
import type { CSSProperties, ReactNode } from "react";

/**
 * Brutalist button: inverted (primary) or outlined (ghost). Mono + uppercase,
 * 1.5px ink border. Hard offset shadow on primary for the editorial feel.
 */
type Variant = "primary" | "ghost";
type Size = "sm" | "md";

function styleFor(variant: Variant, size: Size): CSSProperties {
  return {
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    cursor: "pointer",
    textDecoration: "none",
    fontFamily: "var(--font-mono), monospace",
    fontSize: "var(--fs-xs)",
    letterSpacing: "0.1em",
    textTransform: "uppercase",
    padding: size === "sm" ? "5px 10px" : "9px 16px",
    border: "1.5px solid var(--ink)",
    background: variant === "primary" ? "var(--ink)" : "transparent",
    color: variant === "primary" ? "var(--bg)" : "var(--ink)",
    boxShadow: variant === "primary" ? "var(--shadow-hard)" : "none",
    whiteSpace: "nowrap",
  };
}

export function Button({
  children,
  variant = "primary",
  size = "md",
  onClick,
  type = "button",
  disabled,
  style,
}: {
  children: ReactNode;
  variant?: Variant;
  size?: Size;
  onClick?: () => void;
  type?: "button" | "submit";
  disabled?: boolean;
  style?: CSSProperties;
}) {
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      style={{ ...styleFor(variant, size), ...(disabled ? { opacity: 0.5, cursor: "not-allowed" } : {}), ...style }}
    >
      {children}
    </button>
  );
}

export function ButtonLink({
  children,
  href,
  variant = "primary",
  size = "md",
  style,
}: {
  children: ReactNode;
  href: string;
  variant?: Variant;
  size?: Size;
  style?: CSSProperties;
}) {
  return (
    <Link href={href} style={{ ...styleFor(variant, size), ...style }}>
      {children}
    </Link>
  );
}
