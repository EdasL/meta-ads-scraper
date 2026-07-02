"use server";

import { auth } from "@/lib/auth";

const API = process.env.AGF_API_INTERNAL_URL ?? "http://localhost:8000/api";
const TOKEN = process.env.AGF_API_TOKEN ?? "";

async function requireEmail(): Promise<string> {
  const session = await auth();
  const email = session?.user?.email;
  if (!email) throw new Error("Not signed in.");
  return email;
}

export interface ApiKeyRow {
  key_hash: string;
  label: string;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
}

export async function listKeys(): Promise<ApiKeyRow[]> {
  const email = await requireEmail();
  const res = await fetch(`${API}/internal/keys?email=${encodeURIComponent(email)}`, {
    headers: { Authorization: `Bearer ${TOKEN}` },
    cache: "no-store",
  });
  if (!res.ok) throw new Error("Could not load API keys.");
  return res.json();
}

export async function mintKey(label: string): Promise<string> {
  const email = await requireEmail();
  const res = await fetch(`${API}/internal/keys`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${TOKEN}` },
    body: JSON.stringify({ email, label }),
  });
  if (!res.ok) throw new Error("Could not create the key.");
  return (await res.json()).key as string;
}

export async function revokeKey(keyHash: string): Promise<void> {
  const email = await requireEmail();
  const res = await fetch(`${API}/internal/keys/revoke`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${TOKEN}` },
    body: JSON.stringify({ email, key_hash: keyHash }),
  });
  if (!res.ok && res.status !== 204) throw new Error("Could not revoke the key.");
}
