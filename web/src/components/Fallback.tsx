"use client";

/**
 * Grug error tile shown when a LIVE-mode fetch fails (API down / unreachable /
 * 500). Mock mode never throws, so this only guards the NEXT_PUBLIC_DATA_MODE=live
 * path — without it the data screens hang forever on their loading spinner.
 */
export function Fallback({
  message = "server not answer. maybe sleeping.",
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
      <div className="display-x" style={{ fontSize: "1.9rem", textTransform: "uppercase" }}>
        grug hit wall
      </div>
      <p className="font-mono" style={{ fontSize: "0.74rem", color: "var(--muted)", marginTop: 10 }}>
        {message}
      </p>
      {onRetry && (
        <button
          onClick={onRetry}
          className="display"
          style={{
            marginTop: 18,
            cursor: "pointer",
            background: "var(--ink)",
            color: "var(--paper)",
            border: "1.5px solid var(--ink)",
            padding: "8px 18px",
            fontSize: "0.95rem",
            textTransform: "uppercase",
          }}
        >
          try again
        </button>
      )}
    </div>
  );
}
