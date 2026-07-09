import type { Profile } from "@/lib/users";

export function AccountCard({ profile }: { profile: Profile }) {
  return (
    <section style={{ border: "1.5px solid var(--ink)", background: "var(--paper)", padding: "22px 24px", marginBottom: 28 }}>
      <h2 className="t-sm w-strong" style={{ margin: 0 }}>Account</h2>
      <div style={{ display: "flex", alignItems: "center", gap: 14, marginTop: 14 }}>
        {profile.avatar_url && (
          // eslint-disable-next-line @next/next/no-img-element -- external avatar host, no next/image domain config for one thumbnail
          <img src={profile.avatar_url} alt="" width={44} height={44} style={{ borderRadius: "50%", border: "1.5px solid var(--ink)" }} />
        )}
        <div>
          <p className="w-strong" style={{ margin: 0 }}>{profile.name || profile.email}</p>
          <p className="t-sm w-ui" style={{ color: "var(--muted)", margin: "2px 0 0" }}>
            {profile.email} · signs in with {profile.auth_provider === "password" ? "email" : profile.auth_provider}
          </p>
        </div>
      </div>
    </section>
  );
}
