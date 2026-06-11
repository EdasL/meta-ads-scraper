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

/** Signed count for a raw delta; undefined hides the row (unknown is not 0). */
function signedCount(n: number | undefined): string | undefined {
  if (n === undefined) return undefined;
  return n > 0 ? `+${n.toLocaleString()}` : n.toLocaleString();
}

/** The long-tail maker facts: domain, age, category, Instagram, price, tech
 * stack, and data-freshness rows. The headline numbers (ads in library, page
 * likes, owner, store rating) live in GemHeader. */
export function GemFacts({ gem }: { gem: GemDossier }) {
  // Captured once at mount (lazy init keeps Date.now() out of render purity).
  const [nowMs] = useState(() => Date.now());
  const c = gem.company;
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
  const igValue = c.ig_username ? (
    <a
      href={`https://www.instagram.com/${c.ig_username}`}
      target="_blank"
      rel="noopener noreferrer"
      style={{ color: "var(--ink-soft)", textDecoration: "underline" }}
    >
      @{c.ig_username}
    </a>
  ) : undefined;
  return (
    <section>
      <h2 className="t-md w-strong" style={{ margin: "0 0 12px" }}>Details</h2>
      <div style={{ border: "1px solid var(--line)", background: "var(--paper)", padding: "4px 16px" }}>
        <Fact label="Website" value={siteValue} />
        <Fact label="Site age" value={c.domain_age_years ? `${c.domain_age_years} years` : undefined} />
        <Fact label="Category" value={c.page_category} />
        <Fact label="Instagram" value={igValue} />
        <Fact label="Instagram followers" value={c.ig_followers?.toLocaleString()} />
        <Fact label="Price" value={gem.pricing} />
        <Fact label="Installs (4 weeks)" value={signedCount(gem.installs_delta_28d)} />
        <Fact label="Reviews (4 weeks)" value={signedCount(gem.rating_count_delta_28d)} />
        <Fact
          label="Niche"
          value={gem.niche_keyword && gem.niche_advertiser_count !== undefined
            ? `${gem.niche_keyword} · ${gem.niche_advertiser_count} advertiser${gem.niche_advertiser_count === 1 ? "" : "s"}`
            : undefined}
        />
        <Fact label="Ads checked" value={gem.ads_last_seen ? timeAgo(gem.ads_last_seen, nowMs) : undefined} />
        <Fact label="Store checked" value={storeCheckedAt ? timeAgo(storeCheckedAt, nowMs) : undefined} />
        {c.tech && c.tech.length > 0 && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6, padding: "12px 0" }}>
            {c.tech.map((t) => (
              <span key={t} className="font-mono" style={{ fontSize: "var(--fs-xs)", padding: "3px 8px", border: "1px solid var(--line)", color: "var(--ink-soft)" }}>{t}</span>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
