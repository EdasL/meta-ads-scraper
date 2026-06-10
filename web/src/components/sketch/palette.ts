/**
 * Dark monochrome palette — mirrors globals.css tokens. Used by canvas/SVG
 * sketch components that can't read CSS vars at draw time. Monochrome by
 * design: emphasis is ink/shade, never a colored hue.
 */
export const INK = "#ededed"; // primary text + bold borders (not pure #fff)
export const INK_SOFT = "#d4d4d4";
export const MUTED = "#a3a3a3";
export const FAINT = "#74747e"; // decorative / non-text only
export const PAPER = "#1c1c20"; // card surface (--surface-2)
export const SURFACE_3 = "#232329"; // raised / track fill
export const BG = "#0d0d0f"; // canvas
/** Kept for API compat; monochrome so it maps to ink. */
export const STRING_RED = "#ededed";

/** Semantic trend accents (mirror globals.css --up / --down). Used by SVG
 * charts that can't read CSS vars at draw time. */
export const UP = "#4ade80";
export const DOWN = "#f87171";

export function seedFrom(key: string): number {
  let h = 0;
  for (let i = 0; i < key.length; i++) {
    h = (h << 5) - h + key.charCodeAt(i);
    h |= 0;
  }
  return Math.abs(h) % 100000;
}
