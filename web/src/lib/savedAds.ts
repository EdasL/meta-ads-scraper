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

/**
 * A backend write failed. Rather than restore a snapshot captured before the
 * write (which goes wrong when a save and an unsave for the same ad are in
 * flight together and land out of order), refetch the authoritative list — the
 * server, not the client, decides what is saved. Mirrors saved.ts's
 * `onSyncError`.
 */
function onSyncError(e: unknown) {
  console.error("savedAds: backend sync failed", e);
  hydrated = false; // force the refetch below past the subscribe-time guard
  void hydrate();
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
  rows = [optimistic, ...rows];
  emit();
  upsertSavedAd(externalAdId, { ...snapshot, user_email: email }).catch(onSyncError);
}

export function unsaveAd(externalAdId: string) {
  if (!userEmail) return;
  const before = rows.length;
  rows = rows.filter((r) => r.external_ad_id !== externalAdId);
  if (rows.length === before) return; // not saved — nothing to send
  emit();
  removeSavedAd(externalAdId, userEmail).catch(onSyncError);
}
