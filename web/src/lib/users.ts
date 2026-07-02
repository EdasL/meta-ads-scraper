/**
 * Login verification via the Go API (frontend.users). The web server holds the
 * internal token; the browser never sees it. There is NO public registration —
 * accounts are provisioned in the backend (see the /settings admin page).
 */
const API = process.env.AGF_API_INTERNAL_URL ?? "http://localhost:8000/api";
const TOKEN = process.env.AGF_API_TOKEN ?? "";

export interface VerifiedUser {
  email: string;
  name: string;
  role: "admin" | "member";
}

export async function verifyLogin(
  email: string,
  password: string,
): Promise<VerifiedUser | null> {
  const res = await fetch(`${API}/internal/login`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${TOKEN}`,
    },
    body: JSON.stringify({ email, password }),
    cache: "no-store",
  });
  if (!res.ok) return null;
  return (await res.json()) as VerifiedUser;
}
