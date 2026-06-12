/**
 * Remembers the exact Discover URL (page, filters) and scroll offset across
 * a gem-detail round trip, so "Back to Discover" lands where the user left.
 * sessionStorage-backed: survives client navigation, dies with the tab.
 *
 * Pure over an injected StorageLike so the logic is testable with node --test.
 */

const KEY = "fd.discover.return";

export interface ReturnTo {
  /** pathname + search of the Discover feed, e.g. "/discover?page=3" */
  url: string;
  /** window scroll offset at the moment of leaving */
  scrollY: number;
}

export interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

export function saveReturnTo(value: ReturnTo, store: StorageLike): void {
  try {
    store.setItem(KEY, JSON.stringify(value));
  } catch {
    // Quota / private-mode failures just mean no restore — never break nav.
  }
}

export function loadReturnTo(store: StorageLike): ReturnTo | null {
  try {
    const raw = store.getItem(KEY);
    if (!raw) return null;
    const v: unknown = JSON.parse(raw);
    if (typeof v !== "object" || v === null) return null;
    const { url, scrollY } = v as { url?: unknown; scrollY?: unknown };
    // Only ever send the user back into the Discover feed.
    if (typeof url !== "string" || !url.startsWith("/discover")) return null;
    const y = typeof scrollY === "number" && Number.isFinite(scrollY) && scrollY >= 0 ? scrollY : 0;
    return { url, scrollY: y };
  } catch {
    return null;
  }
}

export function clearReturnTo(store: StorageLike): void {
  try {
    store.removeItem(KEY);
  } catch {
    // ignore
  }
}
