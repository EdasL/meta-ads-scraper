"use client";

/**
 * NRTHVIEW-style ticker: a row of labels scrolling forever. Strict B&W mono.
 * Content is duplicated so the loop is seamless at translateX(-50%).
 */
export function Marquee({
  items,
  reverse = false,
  sep = "/",
  invert = false,
}: {
  items: string[];
  reverse?: boolean;
  sep?: string;
  invert?: boolean;
}) {
  const run = [...items, ...items];
  return (
    <div
      style={{
        overflow: "hidden",
        borderTop: "1.5px solid var(--ink)",
        borderBottom: "1.5px solid var(--ink)",
        background: invert ? "var(--ink)" : "var(--paper)",
        color: invert ? "var(--paper)" : "var(--ink)",
        padding: "7px 0",
      }}
    >
      <div className={`marquee-track${reverse ? " rev" : ""}`}>
        {run.map((label, i) => (
          <span
            key={i}
            className="font-mono"
            style={{ fontSize: "0.7rem", letterSpacing: "0.14em", textTransform: "uppercase", padding: "0 18px" }}
          >
            {label}
            <span style={{ opacity: 0.4, marginLeft: 18 }}>{sep}</span>
          </span>
        ))}
      </div>
    </div>
  );
}
