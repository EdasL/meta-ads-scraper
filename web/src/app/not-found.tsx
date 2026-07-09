import Link from "next/link";

export default function NotFound() {
  return (
    <main style={{ minHeight: "60vh", display: "grid", placeItems: "center", padding: 24, position: "relative", zIndex: 1 }}>
      <div style={{ textAlign: "center" }}>
        <h1 className="display-x" style={{ fontSize: "1.8rem" }}>Page not found</h1>
        <p style={{ color: "var(--muted)", marginTop: 8 }}>Nothing lives at this address.</p>
        <Link href="/discover" className="t-sm w-strong"
          style={{ display: "inline-block", marginTop: 20, background: "var(--ink)", color: "var(--paper)", padding: "12px 22px", textDecoration: "none" }}>
          Go to Discover
        </Link>
      </div>
    </main>
  );
}
