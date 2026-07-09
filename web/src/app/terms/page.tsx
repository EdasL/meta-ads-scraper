const S = {
  main: { maxWidth: 720, margin: "0 auto", padding: "48px 22px 64px", position: "relative" as const, zIndex: 1 },
  h2: { marginTop: 28, fontSize: "1.05rem" },
  p: { marginTop: 10, lineHeight: 1.6, color: "var(--ink-soft)" },
};

export const metadata = { title: "Terms — Field Desk" };

export default function TermsPage() {
  return (
    <main style={S.main}>
      <h1 className="display-x" style={{ fontSize: "2rem" }}>Terms of service</h1>
      <p style={S.p}>Last updated: July 9, 2026.</p>

      <h2 className="w-strong" style={S.h2}>The service</h2>
      <p style={S.p}>
        Field Desk surfaces mobile apps that appear to be scaling paid
        advertising, based on public ad-library and app-store data. The data is
        collected and processed automatically and comes with no guarantee of
        accuracy, completeness, or fitness for any decision you make with it.
      </p>

      <h2 className="w-strong" style={S.h2}>Your account</h2>
      <p style={S.p}>
        You sign in with a Google or GitHub account. You are responsible for
        activity under your account. We may suspend accounts that abuse the
        service — automated scraping of the dashboard, resale of the data, or
        attempts to disrupt the service.
      </p>

      <h2 className="w-strong" style={S.h2}>No warranty</h2>
      <p style={S.p}>
        The service is provided as-is, without warranties of any kind. We are
        not liable for losses arising from its use. We may change or
        discontinue features at any time.
      </p>

      <h2 className="w-strong" style={S.h2}>Contact</h2>
      <p style={S.p}>Questions: elakavicius@gmail.com</p>
    </main>
  );
}
