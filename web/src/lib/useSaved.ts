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
  getPendingMemberships,
  getServerSnapshot,
  getSnapshot,
  subscribe,
  subscribeGroups,
  subscribeLiked,
  type LikedGem,
  type SavedGem,
  type SavedGroup,
} from "./saved";

const EMPTY_PENDING: ReadonlySet<string> = new Set();
const getPendingServerSnapshot = () => EMPTY_PENDING;

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

/** In-flight membership writes (`membershipKey` entries), for pending rows. */
export function usePendingMemberships(): ReadonlySet<string> {
  return useSyncExternalStore(subscribe, getPendingMemberships, getPendingServerSnapshot);
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
  rateLike,
  unrateLike,
} from "./saved";
