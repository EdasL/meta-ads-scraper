"use client";

/**
 * React bindings over the savedAds.ts store. Components read via these hooks
 * and call the store actions (re-exported below) directly.
 */
import { useSyncExternalStore } from "react";
import {
  getSavedAdsServerSnapshot,
  getSavedAdsSnapshot,
  subscribeSavedAds,
} from "./savedAds";
import type { ApiSavedAd } from "@/types/contracts";

export function useSavedAdsList(): ApiSavedAd[] {
  return useSyncExternalStore(subscribeSavedAds, getSavedAdsSnapshot, getSavedAdsServerSnapshot);
}

export function useIsAdSaved(externalAdId: string | undefined): boolean {
  const ads = useSavedAdsList();
  return !!externalAdId && ads.some((a) => a.external_ad_id === externalAdId);
}

export { saveAd, unsaveAd } from "./savedAds";
