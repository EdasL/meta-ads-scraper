"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { signOut } from "next-auth/react";
import { IconBookmark, IconLogout, IconSearch } from "@tabler/icons-react";
import { useSavedList } from "@/lib/useSaved";

export function AppHeader() {
  const pathname = usePathname();
  const savedCount = useSavedList().length;

  const links = [
    { href: "/discover", label: "find", Icon: IconSearch },
    { href: "/saved", label: savedCount ? `saved ${savedCount}` : "saved", Icon: IconBookmark },
  ];

  return (
    <header
      style={{
        position: "sticky",
        top: 0,
        zIndex: 30,
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "12px 22px",
        background: "var(--surface-1)",
        borderBottom: "1.5px solid var(--ink)",
      }}
    >
      <Link href="/discover" className="w-heavy caps" style={{ fontSize: "var(--fs-md)", letterSpacing: "-0.02em", textDecoration: "none", color: "var(--ink)" }}>
        Field Desk
      </Link>
      <nav style={{ display: "flex", gap: 20, alignItems: "center" }}>
        {links.map((l) => {
          const active = pathname === l.href || pathname.startsWith(l.href + "/");
          return (
            <Link
              key={l.href}
              href={l.href}
              className="font-mono caps"
              style={{
                fontSize: "var(--fs-xs)",
                letterSpacing: "0.1em",
                textDecoration: "none",
                color: "var(--ink)",
                borderBottom: active ? "2px solid var(--ink)" : "2px solid transparent",
                paddingBottom: 2,
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
              }}
            >
              <l.Icon size={15} stroke={1.75} />
              {l.label}
            </Link>
          );
        })}
        <button
          onClick={() => signOut({ redirectTo: "/login" })}
          className="font-mono caps"
          style={{
            fontSize: "var(--fs-xs)",
            letterSpacing: "0.1em",
            background: "transparent",
            border: "none",
            borderBottom: "2px solid transparent",
            padding: "0 0 2px",
            cursor: "pointer",
            color: "var(--muted)",
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
          }}
        >
          <IconLogout size={15} stroke={1.75} />
          leave
        </button>
      </nav>
    </header>
  );
}
