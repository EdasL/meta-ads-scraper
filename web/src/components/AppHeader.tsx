"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { signOut } from "next-auth/react";
import { IconBookmark, IconCards, IconLogout, IconMenu2, IconSearch, IconSettings, IconX } from "@tabler/icons-react";
import { useSavedList } from "@/lib/useSaved";

export function AppHeader() {
  const pathname = usePathname();
  const savedCount = useSavedList().length;
  const [menuOpen, setMenuOpen] = useState(false);

  const links = [
    { href: "/discover", label: "Discover", Icon: IconSearch },
    { href: "/review", label: "Review", Icon: IconCards },
    { href: "/saved", label: savedCount ? `Saved · ${savedCount}` : "Saved", Icon: IconBookmark },
    { href: "/settings", label: "Settings", Icon: IconSettings },
  ];

  const linkStyle = (active: boolean): React.CSSProperties => ({
    textDecoration: "none",
    color: active ? "var(--ink)" : "var(--ink-soft)",
    borderBottom: active ? "2px solid var(--ink)" : "2px solid transparent",
    paddingBottom: 2,
    display: "inline-flex",
    alignItems: "center",
    gap: 6,
  });

  const signOutStyle: React.CSSProperties = {
    background: "transparent",
    border: "none",
    borderBottom: "2px solid transparent",
    padding: "0 0 2px",
    cursor: "pointer",
    color: "var(--muted)",
    display: "inline-flex",
    alignItems: "center",
    gap: 6,
  };

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
      <Link href="/discover" className="w-heavy" style={{ fontSize: "var(--fs-md)", letterSpacing: "-0.02em", textDecoration: "none", color: "var(--ink)" }}>
        Field Desk
      </Link>
      <nav className="nav-links">
        {links.map((l) => {
          const active = pathname === l.href || pathname.startsWith(l.href + "/");
          return (
            <Link key={l.href} href={l.href} className={active ? "t-sm w-strong nums" : "t-sm w-ui nums"} style={linkStyle(active)}>
              <l.Icon size={15} stroke={1.75} />
              {l.label}
            </Link>
          );
        })}
        <button onClick={() => signOut({ redirectTo: "/login" })} className="t-sm w-ui" style={signOutStyle}>
          <IconLogout size={15} stroke={1.75} />
          Sign out
        </button>
      </nav>
      <button
        className="nav-burger"
        aria-label={menuOpen ? "Close menu" : "Open menu"}
        aria-expanded={menuOpen}
        onClick={() => setMenuOpen((open) => !open)}
      >
        {menuOpen ? <IconX size={22} stroke={1.75} /> : <IconMenu2 size={22} stroke={1.75} />}
      </button>
      {menuOpen && (
        <nav className="nav-mobile-panel">
          {links.map((l) => {
            const active = pathname === l.href || pathname.startsWith(l.href + "/");
            return (
              <Link
                key={l.href}
                href={l.href}
                className={active ? "t-sm w-strong nums" : "t-sm w-ui nums"}
                style={{ ...linkStyle(active), borderBottom: "1px solid var(--line)", padding: "14px 22px" }}
                onClick={() => setMenuOpen(false)}
              >
                <l.Icon size={15} stroke={1.75} />
                {l.label}
              </Link>
            );
          })}
          <button onClick={() => signOut({ redirectTo: "/login" })} className="t-sm w-ui" style={{ ...signOutStyle, padding: "14px 22px", justifyContent: "flex-start" }}>
            <IconLogout size={15} stroke={1.75} />
            Sign out
          </button>
        </nav>
      )}
    </header>
  );
}
