"use client";

import { useState, type ReactNode } from "react";
import { bareDomain } from "@/lib/links";
import { timeAgo } from "@/lib/format";
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

/** The maker facts: domain, age, owner, category, IG, page likes, price, tech
 * stack, the latest weekly ad metrics (distinct creatives, EU reach), the true
 * ad-library count, and data-freshness rows (ads checked / store checked). */
export function GemFacts({ gem }: { gem: GemDossier }) {
  // Captured once at mount (lazy init keeps Date.now() out of render purity).
  const [nowMs] = useState(() => Date.now());
  const c = gem.company;
  const latest = gem.scaling.length ? gem.scaling[gem.scaling.length - 1] : undefined;
  const distinct = latest?.distinct_creative_count;
  const euReach = latest?.eu_total_reach;
  const adsInLibrary =
    gem.meta_active_ads != null
      ? `${gem.meta_active_ads}${gem.meta_active_ads_capped ? "+" : ""}`
      : undefined;
  const storeCheckedAt = gem.apps[0]?.updated_at;
  const host = bareDomain(c.domain);
  const siteValue = host ? (
    <a
      href={`https://${host}`}
      target="_blank"
      rel="noopener noreferrer"
      style={{ color: "var(--ink-soft)", textDecoration: "underline" }}
    >
      {host}
    </a>
  ) : undefined;
  return (
    <section>
      <h2 className="eyebrow" style={{ margin: "0 0 12px", color: "var(--ink)" }}>Who makes it</h2>
      <div style={{ border: "1.5px solid var(--ink)", background: "var(--paper)", padding: "4px 16px" }}>
        <Fact label="site" value={siteValue} />
        <Fact label="site age" value={c.domain_age_years ? `${c.domain_age_years} yr` : undefined} />
        <Fact label="owner" value={c.parent_company ?? "Independent"} />
        <Fact label="category" value={c.page_category} />
        <Fact label="instagram" value={c.ig_username ? `@${c.ig_username}` : undefined} />
        <Fact label="ig followers" value={c.ig_followers?.toLocaleString()} />
        <Fact label="page likes" value={gem.page_like_count?.toLocaleString()} />
        <Fact label="price" value={gem.pricing} />
        <Fact label="ads this week" value={distinct ? distinct.toLocaleString() : undefined} />
        <Fact label="eu reach" value={euReach ? euReach.toLocaleString() : undefined} />
        <Fact label="ads in library" value={adsInLibrary} />
        <Fact label="ads checked" value={gem.ads_last_seen ? timeAgo(gem.ads_last_seen, nowMs) : undefined} />
        <Fact label="store checked" value={storeCheckedAt ? timeAgo(storeCheckedAt, nowMs) : undefined} />
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
