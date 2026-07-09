import NextAuth from "next-auth";
import Credentials from "next-auth/providers/credentials";
import GitHub from "next-auth/providers/github";
import Google from "next-auth/providers/google";
import { authConfig } from "./auth.config";
import { oauthLogin, verifyLogin } from "./users";

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
      // Google marks verification explicitly; GitHub's provider only returns
      // verified primary emails, so presence is sufficient there.
      if (account.provider === "google") {
        const verified = (profile as { email_verified?: boolean } | null)?.email_verified;
        if (verified !== true) return false;
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
