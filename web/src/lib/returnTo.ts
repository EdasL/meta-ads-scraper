/**
 * Remembers the exact surface URL (page, filters) and scroll offset the user
 * left to open a gem dossier, so the dossier's back link returns there —
 * Discover, Saved or Hunt, whichever they came from.
 * sessionStorage-backed: survives client navigation, dies with the tab.
 *
 * Pure over an injected StorageLike so the logic is testable with node --test.
 */

const KEY = "fd.gem.return";

/** Surfaces a gem dossier can be opened from, and how the back link names them. */
const ORIGINS: Record<string, string> = {
  "/discover": "Discover",
  "/saved": "Saved",
  "/hunt": "Hunt",
  "/review": "Review",
};

/** Where the back link points when nothing was remembered (direct link, reload). */
export const DEFAULT_ORIGIN = "/discover";

export interface ReturnTo {
  /** pathname + search of the origin surface, e.g. "/discover?page=3" */
  url: string;
  /** window scroll offset at the moment of leaving */
  scrollY: number;
}

export interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

/** The origin path a stored URL belongs to, or null if it is not one of ours. */
function originPath(url: string): string | null {
  if (!url.startsWith("/")) return null;
  const path = url.split(/[?#]/)[0].replace(/\/+$/, "");
  return path in ORIGINS ? path : null;
}

/** "Discover" / "Saved" / "Hunt" — the name of the surface a URL belongs to. */
export function originLabel(url: string): string {
  return ORIGINS[originPath(url) ?? DEFAULT_ORIGIN];
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
    // Only ever send the user back into one of our own product surfaces.
    if (typeof url !== "string" || originPath(url) === null) return null;
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

/**
 * Records the surface the user is on right now. Call it as a gem link is
 * clicked — window.location is the truth even on Discover, where filter
 * changes go through history.replaceState and leave the router hooks stale.
 */
export function rememberOrigin(): void {
  if (typeof window === "undefined") return;
  const { pathname, search } = window.location;
  saveReturnTo({ url: `${pathname}${search}`, scrollY: window.scrollY }, window.sessionStorage);
}
