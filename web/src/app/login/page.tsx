import { Suspense } from "react";
import Link from "next/link";
import { OAuthButtons } from "@/components/auth/OAuthButtons";

export default function LoginPage() {
  return (
    <main
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 24,
        position: "relative",
        zIndex: 1,
      }}
    >
      <div style={{ width: "min(440px, 100%)" }}>
        <div style={{ border: "1.5px solid var(--ink)", background: "var(--paper)", boxShadow: "6px 6px 0 var(--ink)" }}>
          <div style={{ padding: "28px 28px 32px" }}>
            <h1 className="display-x" style={{ fontSize: "2.6rem", margin: 0 }}>Field Desk</h1>
            <p className="font-mono" style={{ fontSize: "0.8rem", color: "var(--muted)", marginTop: 8, lineHeight: 1.5 }}>
              Finds mobile apps that are quietly scaling paid ads — before the
              charts notice. Sign in to see them.
            </p>
            <div style={{ marginTop: 24 }}>
              <Suspense fallback={null}>
                <OAuthButtons />
              </Suspense>
            </div>
          </div>
        </div>
        <p className="font-mono" style={{ fontSize: "0.72rem", color: "var(--muted)", marginTop: 14, textAlign: "center" }}>
          By signing in you agree to the{" "}
          <Link href="/terms" style={{ color: "inherit" }}>terms</Link> and{" "}
          <Link href="/privacy" style={{ color: "inherit" }}>privacy policy</Link>.
        </p>
      </div>
    </main>
  );
}
