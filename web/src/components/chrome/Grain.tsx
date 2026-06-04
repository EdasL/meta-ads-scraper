"use client";

/**
 * Film-grain overlay (nrthview grit). SVG fractal-noise tiled over the whole
 * viewport, multiplied at low opacity. Non-interactive, sits above everything.
 */
const NOISE = `<svg xmlns='http://www.w3.org/2000/svg' width='180' height='180'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.82' numOctaves='2' stitchTiles='stitch'/></filter><rect width='100%' height='100%' filter='url(#n)'/></svg>`;

export function Grain() {
  return (
    <div
      aria-hidden
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 9999,
        pointerEvents: "none",
        opacity: 0.06,
        mixBlendMode: "multiply",
        backgroundImage: `url("data:image/svg+xml,${encodeURIComponent(NOISE)}")`,
        backgroundSize: "180px 180px",
      }}
    />
  );
}
