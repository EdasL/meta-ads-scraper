/**
 * Strict black & white palette (NRTHVIEW direction). Mirrors globals.css.
 * Monochrome — connectors and emphasis are ink, never colored.
 */
export const INK = "#0a0a0a";
export const INK_SOFT = "#4a4a4a";
export const MUTED = "#9b9b9b";
export const PAPER = "#ffffff";
/** Kept for API compat; monochrome so it maps to ink. */
export const STRING_RED = "#0a0a0a";

export function seedFrom(key: string): number {
  let h = 0;
  for (let i = 0; i < key.length; i++) {
    h = (h << 5) - h + key.charCodeAt(i);
    h |= 0;
  }
  return Math.abs(h) % 100000;
}
