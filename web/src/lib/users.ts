import bcrypt from "bcryptjs";

/**
 * Seeded users for the internal tool — NO public registration.
 * Provide real users via the AUTH_USERS env var (JSON array of
 * { email, name?, passwordHash }). Otherwise a dev account is created:
 *   email:    founder@fielddesk.local
 *   password: $AUTH_DEV_PASSWORD (default "fielddesk")
 * To add teammates later, generate hashes with:
 *   node -e "console.log(require('bcryptjs').hashSync('PASSWORD',10))"
 */
export interface SeedUser {
  email: string;
  name?: string;
  passwordHash: string;
}

function loadUsers(): SeedUser[] {
  const raw = process.env.AUTH_USERS;
  if (raw) {
    try {
      const parsed = JSON.parse(raw) as SeedUser[];
      if (Array.isArray(parsed) && parsed.length > 0) return parsed;
    } catch {
      // fall through to dev default
    }
  }
  const devPassword = process.env.AUTH_DEV_PASSWORD ?? "fielddesk";
  return [
    {
      email: "founder@fielddesk.local",
      name: "Founder",
      passwordHash: bcrypt.hashSync(devPassword, 10),
    },
  ];
}

const USERS = loadUsers();

export function findUser(email: string): SeedUser | null {
  const e = email.trim().toLowerCase();
  return USERS.find((u) => u.email.toLowerCase() === e) ?? null;
}
