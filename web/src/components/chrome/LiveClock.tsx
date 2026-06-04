"use client";

import { useEffect, useState } from "react";

function fmt(d: Date): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}.${p(d.getMonth() + 1)}.${p(d.getDate())} · ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

/** Live ticking clock in NRTHVIEW technical-mono style. */
export function LiveClock({ className }: { className?: string }) {
  const [now, setNow] = useState<string>("");

  useEffect(() => {
    const tick = () => setNow(fmt(new Date()));
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, []);

  // suppressHydrationWarning: time only renders client-side.
  return (
    <span className={`hud ${className ?? ""}`} suppressHydrationWarning>
      {now || "————.——.—— · ——:——:——"}
    </span>
  );
}
