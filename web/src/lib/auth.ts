import NextAuth from "next-auth";
import Credentials from "next-auth/providers/credentials";
import GitHub from "next-auth/providers/github";
import Google from "next-auth/providers/google";
import { authConfig } from "./auth.config";
import { oauthLogin, verifyLogin } from "./users";

// GitHub's Auth.js provider resolves the account email via GitHub's
// /user/emails endpoint but picks `primary ?? first` WITHOUT checking the
// `verified` field, so an unconfirmed email can reach us. Verify explicitly:
// call /user/emails ourselves and require the matching entry's verified flag.
// Fail closed on every path (no token, fetch/HTTP error, email absent,
// unverified) — an unverified email must never complete sign-in.
async function githubEmailVerified(
  accessToken: string,
  email: string,
): Promise<boolean> {
  try {
    const res = await fetch("https://api.github.com/user/emails", {
      headers: {
        Authorization: `Bearer ${accessToken}`,
        Accept: "application/vnd.github+json",
      },
    });
    if (!res.ok) return false;
    const emails = (await res.json()) as
      | { email?: string; verified?: boolean }[]
      | null;
    if (!Array.isArray(emails)) return false;
    const target = email.toLowerCase();
    const match = emails.find((e) => e.email?.toLowerCase() === target);
    return match?.verified === true;
  } catch {
    return false;
  }
}

export const { handlers, auth, signIn, signOut } = NextAuth({
  ...authConfig,
  session: { strategy: "jwt" },
  providers: [
    Google,
    GitHub,
    // No UI form renders this provider; it remains for e2e tests and
    // emergency access via a direct POST to /api/auth/callback/credentials.
    Credentials({
      credentials: {
        email: { label: "Email", type: "email" },
        password: { label: "Password", type: "password" },
      },
      async authorize(credentials) {
        const email = String(credentials?.email ?? "");
        const password = String(credentials?.password ?? "");
        if (!email || !password) return null;
        const u = await verifyLogin(email, password);
        if (!u) return null;
        return { id: u.email, email: u.email, name: u.name, role: u.role };
      },
    }),
  ],
  callbacks: {
    ...authConfig.callbacks,
    async signIn({ user, account, profile }) {
      if (!account || account.provider === "credentials") return true;
      const email = user.email;
      if (!email) return false;
      // Both providers must supply a verified email (fail closed). Google
      // marks verification explicitly on the profile. GitHub does not — its
      // provider does not check the `verified` flag — so we verify the email
      // ourselves via /user/emails using the OAuth access token.
      if (account.provider === "google") {
        const verified = (profile as { email_verified?: boolean } | null)?.email_verified;
        if (verified !== true) return false;
      }
      if (account.provider === "github") {
        const token = account.access_token;
        if (!token || !(await githubEmailVerified(token, email))) return false;
      }
      const u = await oauthLogin(
        email,
        user.name ?? "",
        account.provider as "google" | "github",
        user.image ?? "",
      );
      if (!u) return false; // fail closed: no account row, no session
      (user as { role?: string }).role = u.role;
      return true;
    },
    jwt({ token, user }) {
      if (user) token.role = (user as { role?: string }).role ?? "member";
      return token;
    },
    session({ session, token }) {
      if (session.user) {
        (session.user as { role?: string }).role = token.role as string;
      }
      return session;
    },
  },
});
