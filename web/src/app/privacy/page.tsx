const S = {
  main: { maxWidth: 720, margin: "0 auto", padding: "48px 22px 64px", position: "relative" as const, zIndex: 1 },
  h2: { marginTop: 28, fontSize: "1.05rem" },
  p: { marginTop: 10, lineHeight: 1.6, color: "var(--ink-soft)" },
};

export const metadata = { title: "Privacy — Field Desk" };

export default function PrivacyPage() {
  return (
    <main style={S.main}>
      <h1 className="display-x" style={{ fontSize: "2rem" }}>Privacy policy</h1>
      <p style={S.p}>Last updated: July 9, 2026.</p>

      <h2 className="w-strong" style={S.h2}>What we store</h2>
      <p style={S.p}>
        When you sign in with Google or GitHub we store your email address,
        name, and avatar. As you use the product we store your app ratings,
        saved lists, and API keys you create. That is the full list.
      </p>

      <h2 className="w-strong" style={S.h2}>What we don&apos;t do</h2>
      <p style={S.p}>
        We don&apos;t sell your data, we don&apos;t share it with third
        parties, and we don&apos;t use third-party analytics or advertising
        trackers. Cookies are used only to keep you signed in.
      </p>

      <h2 className="w-strong" style={S.h2}>Deletion</h2>
      <p style={S.p}>
        Email elakavicius@gmail.com from your account address and we&apos;ll
        delete your account and its data.
      </p>
    </main>
  );
}
