import NextAuth from "next-auth";
import { authConfig } from "@/lib/auth.config";

export default NextAuth(authConfig).auth;

// Run the auth middleware on every app route except API, Next internals, and
// the login page. The `authorized` callback (auth.config.ts) is the single
// source of truth for which paths require a session (its PROTECTED list).
export const config = {
  matcher: ["/((?!api|backend|_next/static|_next/image|favicon.ico|login).*)"],
};
