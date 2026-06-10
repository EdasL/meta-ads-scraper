"use client";

/**
 * Error tile shown when a live fetch fails (API down / unreachable / 500).
 * Without it the data screens would hang forever on their loading spinner.
 */
export function Fallback({
  message = "Server isn't responding. It may be down.",
  onRetry,
}: {
  message?: string;
  onRetry?: () => void;
}) {
  return (
    <div
      style={{
        maxWidth: 520,
        margin: "48px auto",
        border: "1.5px solid var(--ink)",
        background: "var(--paper)",
        padding: "30px 24px",
        textAlign: "center",
      }}
    >
      <div className="t-xl w-heavy">
        Can&apos;t reach the server
      </div>
      <p className="font-mono" style={{ fontSize: "0.74rem", color: "var(--muted)", marginTop: 10 }}>
        {message}
      </p>
      {onRetry && (
        <button
          onClick={onRetry}
          className="w-strong"
          style={{
            marginTop: 18,
            cursor: "pointer",
            background: "var(--ink)",
            color: "var(--paper)",
            border: "1.5px solid var(--ink)",
            padding: "8px 18px",
            fontSize: "0.95rem",
          }}
        >
          Try again
        </button>
      )}
    </div>
  );
}
