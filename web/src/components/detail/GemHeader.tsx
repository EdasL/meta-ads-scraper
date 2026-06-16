"use client";

import { useState } from "react";
import { IconChevronDown, IconChevronRight, IconExternalLink } from "@tabler/icons-react";
import { GEMSCORE_GLOSSARY } from "@/lib/why";
import { markExpandedWhy } from "@/lib/onboarding";
import { buildGemLinks } from "@/lib/links";
import { compactCount, isoDateLabel } from "@/lib/format";
import { AppIcon, InfoDot, ScoreBadge } from "@/components/ui";
import type { GemDossier } from "@/types/contracts";
import { richestApp, StorePresenceChips } from "./AppStorePreview";
import { RefreshDataButton } from "./RefreshDataButton";

/**
 * The dossier header: everything you need to place the app before scrolling.
 * Identity (icon, name, developer, genre, price), the store's own numbers
 * (rating, installs), the GemScore + ad-count trend, the key evidence stats,
 * and the outbound links — one block, no satellite sections.
 */

function Stat({
  label,
  value,
  hint,
  delta,
}: {
  label: string;
  value: React.ReactNode;
  hint?: string;
  delta?: number | null;
}) {
  if (value === undefined || value === null || value === "") return null;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <span className="t-md w-strong nums">{value}</span>
      <span className="eyebrow" style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
        {label}
        {hint && <InfoDot text={hint} label={label} />}
      </span>
      {delta != null && <DeltaLine delta={delta} />}
    </div>
  );
}

/** The trailing 4-week change for a stat — green when growing, muted otherwise.
 * A drop is never alarm-red: a falling ad count is information, not a failure. */
function DeltaLine({ delta }: { delta: number }) {
  const text =
    delta === 0
      ? "no change in 4w"
      : `${delta > 0 ? "+" : "−"}${compactCount(Math.abs(delta))} in 4w`;
  return (
    <span
      className="font-mono nums"
      style={{ fontSize: "var(--fs-xs)", color: delta > 0 ? "var(--up)" : "var(--muted)" }}
    >
      {text}
    </span>
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

export function GemHeader({
  gem,
  advertiserId,
  onRefreshed,
}: {
  gem: GemDossier;
  advertiserId: number;
  onRefreshed: () => void;
}) {
  const [open, setOpen] = useState(false);
  const { explainer } = gem;
  const mc = gem.metric_change_4w; // trailing 4-week change in the ad signals

  const app = richestApp(gem.apps);
  const name = gem.app_name ?? gem.page_name;
  const links = buildGemLinks(gem);
  const adsInLibrary =
    gem.meta_active_ads != null
      ? `${gem.meta_active_ads.toLocaleString()}${gem.meta_active_ads_capped ? "+" : ""}`
      : undefined;

  const metaParts = [gem.cohort, app?.developer, app?.price_label].filter(Boolean);

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
          <ScoreBadge
            score={gem.gem_score}
            variant="detail"
            info={<InfoDot text={GEMSCORE_GLOSSARY} label="what GemScore means" />}
          />
          <RefreshDataButton advertiserId={advertiserId} onRefreshed={onRefreshed} />
          {gem.ads_last_seen && (
            <span className="t-sm" style={{ color: "var(--muted)" }}>
              Data fetched {isoDateLabel(gem.ads_last_seen)}
            </span>
          )}
        </div>
      </div>

      {/* key evidence stats + outbound links, one row */}
      <div
        style={{
          display: "flex",
          gap: "14px 24px",
          flexWrap: "wrap",
          alignItems: "flex-end",
          justifyContent: "space-between",
          marginTop: 16,
          paddingTop: 14,
          borderTop: "1px solid var(--line)",
        }}
      >
        <div style={{ display: "flex", gap: 28, flexWrap: "wrap" }}>
          <Stat label="Ads in library" value={adsInLibrary} />
          <Stat
            label="Active ads"
            value={gem.ad_lifecycle ? gem.ad_lifecycle.active_now.toLocaleString() : undefined}
            delta={mc?.active_ads}
            hint="Ads running right now. The 4-week change shows whether the advertiser is putting more spend behind the app or pulling back."
          />
          <Stat
            label="Ads running 30+ days"
            value={gem.ad_lifecycle ? gem.ad_lifecycle.running_30d_plus.toLocaleString() : undefined}
            delta={mc?.running_30d}
            hint="Ads still live after 30+ days. An advertiser keeps paying to run an ad only when it converts, so long-running ads are proven winners — the strongest sign the app is working. The 4-week change shows whether winners are accumulating."
          />
          <Stat
            label="New ads (last 4 weeks)"
            value={gem.ad_lifecycle ? gem.ad_lifecycle.launched_4w.toLocaleString() : undefined}
            hint="Ads launched in the last 4 weeks — already a 4-week figure. Steady new creative means the advertiser is still testing and scaling spend, not coasting on old ads."
          />
          <Stat label="Page likes" value={gem.page_like_count?.toLocaleString()} />
        </div>
        {links.length > 0 && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
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
      </div>

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
