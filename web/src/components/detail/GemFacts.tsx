"use client";

import type { ReactNode } from "react";
import type { GemDossier } from "@/types/contracts";

function Fact({ label, value }: { label: string; value: ReactNode }) {
  if (value === undefined || value === null || value === "") return null;
  return (
    <div style={{ display: "flex", justifyContent: "space-between", gap: 12, padding: "8px 0", borderBottom: "1px solid var(--line)" }}>
      <span className="eyebrow">{label}</span>
      <span className="font-mono nums t-sm" style={{ textAlign: "right", color: "var(--ink-soft)" }}>{value}</span>
    </div>
  );
}

/** The maker facts: domain, age, owner, category, IG, price, tech stack. */
export function GemFacts({ gem }: { gem: GemDossier }) {
  const c = gem.company;
  return (
    <section>
      <h2 className="eyebrow" style={{ margin: "0 0 12px", color: "var(--ink)" }}>who make it</h2>
      <div style={{ border: "1.5px solid var(--ink)", background: "var(--paper)", padding: "4px 16px" }}>
        <Fact label="site" value={c.domain} />
        <Fact label="site age" value={c.domain_age_years ? `${c.domain_age_years} yr` : undefined} />
        <Fact label="owner" value={c.parent_company ?? "nobody. just them"} />
        <Fact label="kind" value={c.page_category} />
        <Fact label="instagram" value={c.ig_username ? `@${c.ig_username}` : undefined} />
        <Fact label="ig follower" value={c.ig_followers?.toLocaleString()} />
        <Fact label="price" value={gem.pricing} />
        {c.tech && c.tech.length > 0 && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6, padding: "12px 0" }}>
            {c.tech.map((t) => (
              <span key={t} className="font-mono caps" style={{ fontSize: "var(--fs-xs)", padding: "3px 8px", border: "1px solid var(--ink)" }}>{t}</span>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
