"use client";

import { useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { signIn } from "next-auth/react";
import { INK } from "@/components/sketch/palette";
import { IconArrowRight } from "@tabler/icons-react";

export function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const callbackUrl = params.get("callbackUrl") ?? "/discover";

  const [email, setEmail] = useState("founder@fielddesk.local");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const res = await signIn("credentials", { email, password, redirect: false });
    setBusy(false);
    if (res?.error) {
      setError("that not right. try again.");
      return;
    }
    router.push(callbackUrl);
    router.refresh();
  }

  const field: React.CSSProperties = {
    width: "100%",
    background: "transparent",
    border: `1.5px solid ${INK}`,
    padding: "11px 12px",
    fontSize: "0.95rem",
    marginTop: 6,
    fontFamily: "var(--font-mono), monospace",
  };

  return (
    <div style={{ width: "min(440px, 100%)" }}>
      <div className="eyebrow" style={{ color: INK, marginBottom: 14 }}>only for us</div>
      <div style={{ border: `1.5px solid ${INK}`, background: "var(--paper)", boxShadow: "6px 6px 0 var(--ink)" }}>
        <form onSubmit={onSubmit} style={{ padding: "28px 28px 32px" }}>
          <h1 className="display-x" style={{ fontSize: "2.6rem", margin: 0 }}>Field Desk</h1>
          <p className="font-mono" style={{ fontSize: "0.68rem", color: "var(--muted)", marginTop: 6, textTransform: "uppercase", letterSpacing: "0.06em" }}>
            say who you are. then grug show gem.
          </p>

          <label className="eyebrow" style={{ display: "block", marginTop: 22, color: INK }}>
            your email
            <input style={field} type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="username" required />
          </label>

          <label className="eyebrow" style={{ display: "block", marginTop: 16, color: INK }}>
            secret word
            <input style={field} type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
          </label>

          {error && (
            <p className="font-mono" style={{ color: INK, fontSize: "0.78rem", marginTop: 14, fontWeight: 600 }}>{error}</p>
          )}

          <button
            type="submit"
            disabled={busy}
            className="display"
            style={{
              width: "100%",
              marginTop: 24,
              padding: "14px",
              fontSize: "1.2rem",
              textTransform: "uppercase",
              background: INK,
              color: "var(--paper)",
              border: "none",
              cursor: busy ? "wait" : "pointer",
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              gap: 8,
            }}
          >
            {busy ? "grug think…" : <>go in <IconArrowRight size={20} stroke={2} /></>}
          </button>
        </form>
      </div>
    </div>
  );
}
