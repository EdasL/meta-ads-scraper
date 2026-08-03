"use client";

/**
 * React bindings over the saved.ts store. Components read via these hooks
 * (reactive across the app + across tabs) and call the store actions directly.
 */
import { useSyncExternalStore } from "react";
import {
  getGroupsServerSnapshot,
  getGroupsSnapshot,
  getServerSnapshot,
  getSnapshot,
  subscribe,
  subscribeGroups,
  type SavedGem,
  type SavedGroup,
} from "./saved";

export function useSavedList(): SavedGem[] {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}

/** The board's columns, in board order. Empty until the store hydrates. */
export function useSavedGroups(): SavedGroup[] {
  return useSyncExternalStore(subscribeGroups, getGroupsSnapshot, getGroupsServerSnapshot);
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
