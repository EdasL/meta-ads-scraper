"use client";

/**
 * React bindings over the saved.ts store. Components read via these hooks
 * (reactive across the app + across tabs) and call the store actions directly.
 */
import { useSyncExternalStore } from "react";
import {
  getGroupsServerSnapshot,
  getGroupsSnapshot,
  getLikedServerSnapshot,
  getLikedSnapshot,
  getServerSnapshot,
  getSnapshot,
  subscribe,
  subscribeGroups,
  subscribeLiked,
  type LikedGem,
  type SavedGem,
  type SavedGroup,
} from "./saved";

export function useSavedList(): SavedGem[] {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}

/** The user's folders, in display order. Empty until the store hydrates. */
export function useSavedGroups(): SavedGroup[] {
  return useSyncExternalStore(subscribeGroups, getGroupsSnapshot, getGroupsServerSnapshot);
}

/** The built-in Liked collection (rated_at DESC). Empty until hydrated. */
export function useLikedGems(): LikedGem[] {
  return useSyncExternalStore(subscribeLiked, getLikedSnapshot, getLikedServerSnapshot);
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
  setVerdict,
  setConfidence,
  allTags,
  listGroups,
  createGroup,
  renameGroup,
  deleteGroup,
  addToFolder,
  removeFromFolder,
} from "./saved";
