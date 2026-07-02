import "next-auth";

declare module "next-auth" {
  interface User {
    role?: "admin" | "member";
  }
  interface Session {
    user?: {
      email?: string | null;
      name?: string | null;
      role?: "admin" | "member";
    };
  }
}

declare module "next-auth/jwt" {
  interface JWT {
    role?: "admin" | "member";
  }
}
