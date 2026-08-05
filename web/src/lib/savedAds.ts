"use client";

/**
 * Saved ads store — the ad-level sibling of saved.ts, flat by design (no
 * folders, no ratings). Optimistic writes: the list updates immediately and
 * reverts if the API call fails.
 */
import { getSavedAds, removeSavedAd, upsertSavedAd } from "@/lib/api";
import type { ApiSavedAd, ApiSavedAdIn } from "@/types/contracts";

type Listener = () => void;

let userEmail: string | null = null;
let rows: ApiSavedAd[] = [];
let hydrated = false;
const listeners = new Set<Listener>();

const EMPTY: ApiSavedAd[] = [];

function emit() {
  for (const l of listeners) l();
}

export function subscribeSavedAds(l: Listener): () => void {
  listeners.add(l);
  if (!hydrated) void hydrate();
  return () => {
    listeners.delete(l);
  };
}

export function getSavedAdsSnapshot(): ApiSavedAd[] {
  return rows;
}

export function getSavedAdsServerSnapshot(): ApiSavedAd[] {
  return EMPTY;
}

async function hydrate() {
  if (!userEmail) return; // wait until the session user is known
  hydrated = true;
  try {
    rows = await getSavedAds(userEmail);
    emit();
  } catch {
    hydrated = false; // retry on the next subscribe
  }
}

/** Set the current user (saved ads are keyed by email) and refetch. */
export function setSavedAdsUser(email: string | null) {
  if (email === userEmail) return;
  userEmail = email;
  rows = [];
  hydrated = false;
  emit();
  if (email) void hydrate();
}

export function saveAd(externalAdId: string, snapshot: Omit<ApiSavedAdIn, "user_email">) {
  if (!userEmail || rows.some((r) => r.external_ad_id === externalAdId)) return;
  const email = userEmail;
  const optimistic: ApiSavedAd = {
    ...snapshot,
    user_email: email,
    external_ad_id: externalAdId,
    saved_at: new Date().toISOString(),
  };
  const prev = rows;
  rows = [optimistic, ...rows];
  emit();
  upsertSavedAd(externalAdId, { ...snapshot, user_email: email }).catch(() => {
    rows = prev;
    emit();
  });
}

export function unsaveAd(externalAdId: string) {
  if (!userEmail) return;
  const prev = rows;
  rows = rows.filter((r) => r.external_ad_id !== externalAdId);
  if (rows.length === prev.length) return;
  emit();
  removeSavedAd(externalAdId, userEmail).catch(() => {
    rows = prev;
    emit();
  });
}
