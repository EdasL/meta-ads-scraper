"use client";

/**
 * Saved ads store — the ad-level sibling of saved.ts, flat by design (no
 * folders, no ratings). Optimistic writes: the list updates immediately, and a
 * failed write refetches the authoritative list from the server.
 */
import { getSavedAds, removeSavedAd, upsertSavedAd } from "@/lib/api";
import type { ApiSavedAd, ApiSavedAdIn } from "@/types/contracts";

type Listener = () => void;

let userEmail: string | null = null;
let rows: ApiSavedAd[] = [];
/** The user whose list is hydrated OR currently being fetched — the synchronous
 *  dedupe key, so overlapping hydrate() calls collapse into one request
 *  (saved.ts's `hydratedKey`). Null means "not hydrated, no fetch in flight". */
let hydratedKey: string | null = null;
/** True while a getSavedAds() is awaiting, so onSyncError can queue a follow-up
 *  instead of racing a second request against it. */
let fetching = false;
/** A write failed while a fetch was in flight: that fetch may have been issued
 *  before the failed write landed, so run exactly one more after it. */
let resyncQueued = false;
const listeners = new Set<Listener>();

const EMPTY: ApiSavedAd[] = [];

function emit() {
  for (const l of listeners) l();
}

export function subscribeSavedAds(l: Listener): () => void {
  listeners.add(l);
  void hydrate(); // hydrate() dedupes on hydratedKey, so this is cheap per subscriber
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
  const key = userEmail ?? "";
  // Synchronous dedupe, before any await: a fetch for this same user already
  // owns the key, so concurrent callers collapse into it rather than racing
  // two GETs whose last-resolving response would win (saved.ts:238-241).
  if (hydratedKey === key) return;
  hydratedKey = key;
  if (!userEmail) return; // wait until the session user is known
  const email = userEmail; // captured while narrowed, before any await

  do {
    resyncQueued = false;
    fetching = true;
    try {
      const fetched = await getSavedAds(email);
      if (userEmail !== email) return; // user switched mid-flight — drop the stale response
      rows = fetched;
      emit();
    } catch (e) {
      console.error("savedAds: hydrate failed", e);
      // Only clear the key we still own: on a mid-flight user switch the new
      // hydrate has already claimed it and must not be cancelled.
      if (userEmail === email) hydratedKey = null; // allow a retry on the next subscribe
      return;
    } finally {
      fetching = false;
    }
  } while (resyncQueued);
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
  if (fetching) {
    // A GET is already out, but it may have been issued before this write
    // failed, so its response can predate the server's real state. Queue one
    // more pass instead of starting an overlapping request.
    resyncQueued = true;
    return;
  }
  hydratedKey = null; // force the refetch past hydrate()'s dedupe guard
  void hydrate();
}

/** Set the current user (saved ads are keyed by email) and refetch. */
export function setSavedAdsUser(email: string | null) {
  if (email === userEmail) return;
  userEmail = email;
  rows = [];
  hydratedKey = null;
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
