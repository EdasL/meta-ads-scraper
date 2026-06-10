"use client";

import { useState } from "react";
import { IconChevronDown, IconChevronRight, IconExternalLink } from "@tabler/icons-react";
import { GEMSCORE_GLOSSARY, growthLine } from "@/lib/why";
import { markExpandedWhy } from "@/lib/onboarding";
import { buildGemLinks } from "@/lib/links";
import { compactCount } from "@/lib/format";
import { AppIcon, InfoDot, ScoreBadge, Trend } from "@/components/ui";
import type { GemDossier } from "@/types/contracts";
import { richestApp, StorePresenceChips } from "./AppStorePreview";

/**
 * The dossier header: everything you need to place the app before scrolling.
 * Identity (icon, name, developer, genre, price), the store's own numbers
 * (rating, installs), the GemScore + ad-count trend, the key evidence stats,
 * and the outbound links — one block, no satellite sections.
 */

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  if (value === undefined || value === null || value === "") return null;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <span className="t-md w-strong nums">{value}</span>
      <span className="eyebrow">{label}</span>
    </div>
  );
}

function ExplainerField({ label, value }: { label: string; value: string }) {
  if (!value) return null;
  return (
    <div>
      <div className="eyebrow" style={{ color: "var(--ink)", marginBottom: 4 }}>{label}</div>
      <p className="t-sm measure" style={{ margin: 0, color: "var(--ink-soft)" }}>{value}</p>
    </div>
  );
}

export function GemHeader({ gem }: { gem: GemDossier }) {
  const [open, setOpen] = useState(false);
  const { explainer } = gem;

  const app = richestApp(gem.apps);
  const name = gem.app_name ?? gem.page_name;
  const links = buildGemLinks(gem);
  const latest = gem.scaling.length ? gem.scaling[gem.scaling.length - 1] : undefined;
  const adsInLibrary =
    gem.meta_active_ads != null
      ? `${gem.meta_active_ads.toLocaleString()}${gem.meta_active_ads_capped ? "+" : ""}`
      : undefined;

  const metaParts = [gem.cohort, app?.developer, app?.price_label].filter(Boolean);

  // Same observed-growth line the feed cards use — one source of truth.
  const g = growthLine(gem.growth, gem);
  const trendLabel = g.kind === "growth" ? g.text : `${g.text} ads live`;

  return (
    <section style={{ border: "1.5px solid var(--ink)", background: "var(--surface-1)", padding: "20px 22px", boxShadow: "var(--shadow-hard)" }}>
      {/* identity row */}
      <div style={{ display: "flex", gap: 18, alignItems: "flex-start", flexWrap: "wrap" }}>
        <AppIcon src={app?.icon_url ?? gem.icon_url} name={name} size={64} />
        <div style={{ flex: "1 1 280px", minWidth: 0 }}>
          <h1 className="t-2xl w-heavy" style={{ margin: 0, wordBreak: "break-word" }}>{name}</h1>
          {metaParts.length > 0 && (
            <div className="t-sm" style={{ color: "var(--muted)", marginTop: 4 }}>{metaParts.join(" · ")}</div>
          )}
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", marginTop: 8 }}>
            <StorePresenceChips apps={gem.apps} />
            {app?.rating != null && (
              <span className="font-mono nums t-sm" style={{ color: "var(--ink-soft)" }}>
                ★ {app.rating.toFixed(1)}
                {app.rating_count != null && ` (${compactCount(app.rating_count)} ratings)`}
              </span>
            )}
            {app?.installs != null && (
              <span className="font-mono nums t-sm" style={{ color: "var(--ink-soft)" }}>
                ≈{compactCount(app.installs)} installs
              </span>
            )}
          </div>
        </div>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 8 }}>
          <ScoreBadge score={gem.gem_score} variant="detail" />
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <Trend glyph={g.glyph} label={trendLabel} tone={g.tone} />
            <InfoDot text={GEMSCORE_GLOSSARY} label="what GemScore means" />
          </div>
        </div>
      </div>

      {/* key evidence stats */}
      <div style={{ display: "flex", gap: 32, flexWrap: "wrap", marginTop: 18, paddingTop: 14, borderTop: "1px solid var(--line)" }}>
        <Stat label="Ads in library" value={adsInLibrary} />
        <Stat label="Ads this week" value={latest?.distinct_creative_count ? latest.distinct_creative_count.toLocaleString() : undefined} />
        <Stat label="Page likes" value={gem.page_like_count?.toLocaleString()} />
        <Stat label="Owner" value={gem.company.parent_company ?? "Independent"} />
      </div>

      {/* outbound links */}
      {links.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 16 }}>
          {links.map((l) => (
            <a
              key={l.label}
              href={l.href}
              target="_blank"
              rel="noopener noreferrer"
              className="font-mono"
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
                padding: "7px 12px",
                border: "1px solid var(--ink)",
                background: "var(--paper)",
                color: "var(--ink)",
                textDecoration: "none",
                fontSize: "var(--fs-xs)",
              }}
            >
              {l.label}
              <IconExternalLink size={13} stroke={2} />
            </a>
          ))}
        </div>
      )}

      {/* generated explainer */}
      {explainer && (
        <div style={{ marginTop: 16 }}>
          <button
            type="button"
            onClick={() => {
              setOpen((o) => {
                if (!o) markExpandedWhy();
                return !o;
              });
            }}
            aria-expanded={open}
            className="font-mono"
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 5,
              background: "transparent",
              border: "none",
              cursor: "pointer",
              color: "var(--muted)",
              fontSize: "var(--fs-xs)",
              padding: 0,
            }}
          >
            {open ? <IconChevronDown size={14} /> : <IconChevronRight size={14} />}
            Why this might be working
          </button>

          {open && (
            <div style={{ marginTop: 12, paddingTop: 12, borderTop: "1px solid var(--line)", display: "flex", flexDirection: "column", gap: 12 }}>
              <ExplainerField label="What it does" value={explainer.what_it_does} />
              <ExplainerField label="Problem it solves" value={explainer.problem_solved} />
              <ExplainerField label="Why the ads suggest traction" value={explainer.why_working} />
              <p className="font-mono" style={{ margin: 0, fontSize: "var(--fs-xs)", color: "var(--muted)" }}>
                Generated from this app&apos;s ads and store listing.
              </p>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
