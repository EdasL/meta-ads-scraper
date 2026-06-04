"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { signOut } from "next-auth/react";

const LINKS = [
  { href: "/board", label: "board" },
  { href: "/review", label: "the pile" },
];

export function AppHeader() {
  const pathname = usePathname();
  return (
    <header
      style={{
        position: "sticky",
        top: 0,
        zIndex: 30,
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "14px 22px",
        background: "var(--paper)",
        borderBottom: "1.5px solid var(--ink)",
      }}
    >
      <Link href="/" className="display" style={{ fontSize: "1.2rem", textTransform: "uppercase", textDecoration: "none", color: "var(--ink)" }}>
        Field Desk
      </Link>
      <nav style={{ display: "flex", gap: 22, alignItems: "center" }}>
        {LINKS.map((l) => {
          const active = pathname === l.href || pathname.startsWith(l.href + "/");
          return (
            <Link
              key={l.href}
              href={l.href}
              className="font-mono"
              style={{
                fontSize: "0.74rem",
                letterSpacing: "0.1em",
                textTransform: "uppercase",
                textDecoration: "none",
                color: "var(--ink)",
                borderBottom: active ? "2px solid var(--ink)" : "2px solid transparent",
                paddingBottom: 2,
              }}
            >
              {l.label}
            </Link>
          );
        })}
        <button
          onClick={() => signOut({ redirectTo: "/login" })}
          className="font-mono"
          style={{
            fontSize: "0.74rem",
            letterSpacing: "0.1em",
            textTransform: "uppercase",
            background: "transparent",
            border: "none",
            cursor: "pointer",
            color: "var(--muted)",
          }}
        >
          leave
        </button>
      </nav>
    </header>
  );
}
