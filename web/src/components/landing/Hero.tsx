import { ButtonLink } from "@/components/ui";

/** Thin signed-out entry. One editorial display line + the pitch + go-in.
 * (Signed-in users are redirected to /discover by the / route.) */
export function Hero() {
  const points = ["proven by real ad spend", "caught within weeks", "before everyone copies it"];
  return (
    <main
      style={{
        minHeight: "100vh",
        display: "flex",
        flexDirection: "column",
        justifyContent: "center",
        maxWidth: 920,
        margin: "0 auto",
        padding: "40px 24px",
        position: "relative",
        zIndex: 1,
      }}
    >
      <div className="eyebrow">field desk</div>
      <h1 className="t-4xl w-heavy caps" style={{ margin: "14px 0 0", maxWidth: "15ch" }}>
        find ideas that already work
      </h1>
      <p className="t-md" style={{ marginTop: 18, color: "var(--ink-soft)", maxWidth: "46ch" }}>
        Small apps get big quietly on Meta ads. Field Desk watches the spend, catches them early, and shows you the evidence.
      </p>
      <div style={{ display: "flex", gap: 12, marginTop: 28, flexWrap: "wrap" }}>
        <ButtonLink href="/login">sign in</ButtonLink>
      </div>
      <ul style={{ listStyle: "none", padding: 0, margin: "44px 0 0", display: "flex", flexWrap: "wrap", gap: "10px 24px" }}>
        {points.map((t) => (
          <li key={t} className="font-mono" style={{ fontSize: "var(--fs-xs)", color: "var(--muted)" }}>— {t}</li>
        ))}
      </ul>
    </main>
  );
}
