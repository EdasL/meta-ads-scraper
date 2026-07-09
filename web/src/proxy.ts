import NextAuth from "next-auth";
import { authConfig } from "@/lib/auth.config";

export default NextAuth(authConfig).auth;

// Next 16 proxy (formerly middleware.ts — dev ignores the old filename; prod
// builds only honored it via a deprecated compat shim). Runs the auth check on
// every app route except API, Next internals, and the login page. The
// `authorized` callback (auth.config.ts) is the single source of truth for
// which paths require a session (its PROTECTED list).
export const config = {
  matcher: ["/((?!api|backend|_next/static|_next/image|favicon.ico|login).*)"],
};
