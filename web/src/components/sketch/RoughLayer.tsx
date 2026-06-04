"use client";

import { useEffect, useRef } from "react";
import rough from "roughjs";
import type { RoughSVG } from "roughjs/bin/svg";

export type RoughDraw = (
  rc: RoughSVG,
  width: number,
  height: number,
  svg: SVGSVGElement,
) => void;

/**
 * Renders a hand-drawn SVG overlay sized to its positioned parent.
 * The parent must be `position: relative`. Redraws on resize.
 */
export function RoughLayer({
  draw,
  className,
}: {
  draw: RoughDraw;
  className?: string;
}) {
  const svgRef = useRef<SVGSVGElement>(null);
  const drawRef = useRef(draw);

  useEffect(() => {
    drawRef.current = draw;
  });

  useEffect(() => {
    const svg = svgRef.current;
    const parent = svg?.parentElement;
    if (!svg || !parent) return;

    const render = () => {
      const { width, height } = parent.getBoundingClientRect();
      if (!width || !height) return;
      svg.setAttribute("width", String(width));
      svg.setAttribute("height", String(height));
      svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
      while (svg.firstChild) svg.removeChild(svg.firstChild);
      const rc = rough.svg(svg);
      drawRef.current(rc, width, height, svg);
    };

    render();
    const ro = new ResizeObserver(render);
    ro.observe(parent);
    return () => ro.disconnect();
  }, []);

  return (
    <svg
      ref={svgRef}
      aria-hidden
      className={className}
      style={{
        position: "absolute",
        inset: 0,
        overflow: "visible",
        pointerEvents: "none",
      }}
    />
  );
}
