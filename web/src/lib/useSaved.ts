"use client";

/**
 * React bindings over the saved.ts store. Components read via these hooks
 * (reactive across the app + across tabs) and call the store actions directly.
 */
import { useSyncExternalStore } from "react";
import { getServerSnapshot, getSnapshot, subscribe, type SavedGem } from "./saved";

export function useSavedList(): SavedGem[] {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}

export function useIsSaved(advertiserId: number): boolean {
  return useSavedList().some((g) => g.advertiserId === advertiserId);
}

export function useSavedGem(advertiserId: number): SavedGem | undefined {
  return useSavedList().find((g) => g.advertiserId === advertiserId);
}

export {
  save,
  unsave,
  toggleSave,
  setNote,
  addTag,
  removeTag,
  setStage,
  setVerdict,
  setConfidence,
  allTags,
} from "./saved";
