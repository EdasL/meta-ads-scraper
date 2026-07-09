"use client";

import { useState } from "react";
import { useSearchParams } from "next/navigation";
import { signIn } from "next-auth/react";
import { IconBrandGithub, IconBrandGoogleFilled } from "@tabler/icons-react";

export function OAuthButtons() {
  const params = useSearchParams();
  const callbackUrl = params.get("callbackUrl") ?? "/discover";
  const failed = params.get("error") !== null;
  const [busy, setBusy] = useState<"google" | "github" | null>(null);

  function start(provider: "google" | "github") {
    setBusy(provider);
    // Redirects away; busy state only matters until the browser leaves.
    void signIn(provider, { redirectTo: callbackUrl });
  }

  const button: React.CSSProperties = {
    width: "100%",
    padding: "13px 16px",
    fontSize: "1rem",
    background: "var(--ink)",
    color: "var(--paper)",
    border: "none",
    cursor: "pointer",
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    gap: 10,
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <button className="display" style={button} disabled={busy !== null} onClick={() => start("google")}>
        <IconBrandGoogleFilled size={18} />
        {busy === "google" ? "Redirecting…" : "Continue with Google"}
      </button>
      <button className="display" style={button} disabled={busy !== null} onClick={() => start("github")}>
        <IconBrandGithub size={18} stroke={1.75} />
        {busy === "github" ? "Redirecting…" : "Continue with GitHub"}
      </button>
      {failed && (
        <p className="font-mono" style={{ color: "var(--ink)", fontSize: "0.78rem", margin: 0, fontWeight: 600 }}>
          Sign-in didn&apos;t work. Try again.
        </p>
      )}
    </div>
  );
}
