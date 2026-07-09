"use client";

export default function Error({ reset }: { error: Error; reset: () => void }) {
  return (
    <main style={{ minHeight: "60vh", display: "grid", placeItems: "center", padding: 24, position: "relative", zIndex: 1 }}>
      <div style={{ textAlign: "center" }}>
        <h1 className="display-x" style={{ fontSize: "1.8rem" }}>Something went wrong</h1>
        <p style={{ color: "var(--muted)", marginTop: 8 }}>The error is on our side, not yours.</p>
        <button className="t-sm w-strong" onClick={reset}
          style={{ marginTop: 20, background: "var(--ink)", color: "var(--paper)", border: "none", padding: "12px 22px", cursor: "pointer" }}>
          Try again
        </button>
      </div>
    </main>
  );
}
