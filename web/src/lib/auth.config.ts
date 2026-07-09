import type { NextAuthConfig } from "next-auth";

const PROTECTED = ["/discover", "/saved", "/gems", "/review", "/settings", "/onboarding"];

/**
 * Edge-safe config (no bcrypt / node deps) — shared by middleware and the
 * full auth instance. Route protection lives in the `authorized` callback.
 */
export const authConfig = {
  trustHost: true,
  pages: { signIn: "/login" },
  callbacks: {
    authorized({ auth, request: { nextUrl } }) {
      const isLoggedIn = !!auth?.user;
      const isProtected = PROTECTED.some((p) => nextUrl.pathname.startsWith(p));
      if (isProtected) return isLoggedIn;
      return true;
    },
  },
  providers: [],
} satisfies NextAuthConfig;
