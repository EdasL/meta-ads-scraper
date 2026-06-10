"use client";

import { useState } from "react";
import { SketchLineChart, type ChartPoint } from "@/components/sketch";
import { trendFromScaling } from "@/lib/why";
import { adRunLine, creativeMetaLine, monthDayLabel } from "@/lib/format";
import { adLibraryAdUrl, bareDomain } from "@/lib/links";
import type { Creative, GemDossier } from "@/types/contracts";

/**
 * The evidence sections of the dossier: the ad-count curve (with per-week
 * tooltips naming the exact ads that launched) and the real ad record — one
 * card per ad, the Ad Library way: status, run dates, the creative (or the ad
 * copy when Meta no longer serves media for an ended ad), CTA, and a link to
 * the ad's own library page.
 */
function fmtWeek(iso: string) {
  const d = new Date(iso);
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

/** How many ad cards render before the "Show all" expander. */
const INITIAL_ADS = 12;

function SectionTitle({ children }: { children: React.ReactNode }) {
  return <h2 className="t-md w-strong" style={{ margin: "0 0 12px" }}>{children}</h2>;
}

function StatusPill({ active }: { active: boolean | undefined }) {
  // Unknown status (older rows without is_active) shows nothing — never guess.
  if (active === undefined) return null;
  return (
    <span
      className="font-mono"
      style={{
        fontSize: "0.62rem",
        padding: "2px 7px",
        border: "1px solid",
        borderColor: active ? "var(--up)" : "var(--line)",
        background: active ? "var(--up-bg)" : "transparent",
        color: active ? "var(--up)" : "var(--muted)",
      }}
    >
      {active ? "Active" : "Ended"}
    </span>
  );
}

// Natural aspect ratio — reels are 9:16; a fixed landscape crop hides most of the ad.
const mediaStyle: React.CSSProperties = {
  width: "100%",
  height: "auto",
  display: "block",
  borderTop: "1px solid var(--line)",
  borderBottom: "1px solid var(--line)",
};

/** "INSTALL NOW" / "Install Now" → "Install now" — store CTAs come shouting. */
function sentenceCase(s: string): string {
  const t = s.trim();
  return t ? t[0].toUpperCase() + t.slice(1).toLowerCase() : t;
}

function AdCard({ c }: { c: Creative }) {
  const [broken, setBroken] = useState(false);
  const hasMedia = !!c.snapshot_image_url && !broken;
  const alt = c.title ?? c.body_text ?? "ad creative";
  const run = adRunLine(c);
  // Reach + platforms only — US ads carry neither, so this often stays empty.
  const facts = creativeMetaLine({
    eu_total_reach: c.eu_total_reach,
    publisher_platforms: c.publisher_platforms,
  });
  const ctaDomain = c.link_url ? bareDomain(c.link_url).split("/")[0] : "";
  return (
    <div
      style={{
        border: "1px solid var(--line)",
        background: "var(--paper)",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, padding: "8px 10px" }}>
        <StatusPill active={c.ad_active} />
        {c.external_ad_id && (
          <a
            href={adLibraryAdUrl(c.external_ad_id)}
            target="_blank"
            rel="noopener noreferrer"
            className="font-mono"
            style={{ fontSize: "0.62rem", color: "var(--muted)", textDecoration: "underline" }}
          >
            Ad Library ↗
          </a>
        )}
      </div>
      {run && (
        <div className="font-mono nums" style={{ fontSize: "0.66rem", color: "var(--ink-soft)", padding: "0 10px 8px" }}>
          {run}
        </div>
      )}
      {hasMedia &&
        (c.type === "video" ? (
          <video
            src={c.snapshot_image_url}
            controls
            preload="metadata"
            playsInline
            onError={() => setBroken(true)}
            style={mediaStyle}
          />
        ) : c.external_ad_id ? (
          <a href={adLibraryAdUrl(c.external_ad_id)} target="_blank" rel="noopener noreferrer">
            {/* eslint-disable-next-line @next/next/no-img-element -- dynamic /api/media proxy thumbnails; next/image is a poor fit for an internal tool */}
            <img src={c.snapshot_image_url} alt={alt} loading="lazy" onError={() => setBroken(true)} style={mediaStyle} />
          </a>
        ) : (
          // eslint-disable-next-line @next/next/no-img-element -- dynamic /api/media proxy thumbnails; next/image is a poor fit for an internal tool
          <img src={c.snapshot_image_url} alt={alt} loading="lazy" onError={() => setBroken(true)} style={mediaStyle} />
        ))}
      {c.body_text && (
        <div
          style={{
            fontSize: "var(--fs-xs)",
            color: "var(--ink-soft)",
            padding: "8px 10px",
            lineHeight: 1.45,
            display: "-webkit-box",
            WebkitLineClamp: 3,
            WebkitBoxOrient: "vertical",
            overflow: "hidden",
          }}
        >
          {c.body_text}
        </div>
      )}
      {!hasMedia && !c.body_text && (
        <div style={{ fontSize: "var(--fs-xs)", color: "var(--muted)", padding: "8px 10px" }}>
          {c.title ?? "Creative not available for this ad."}
        </div>
      )}
      {facts && (
        <div className="font-mono nums" style={{ fontSize: "0.62rem", color: "var(--muted)", padding: "0 10px 8px" }}>
          {facts}
        </div>
      )}
      {(c.cta_text || ctaDomain) && (
        <div
          style={{
            marginTop: "auto",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 8,
            padding: "8px 10px",
            borderTop: "1px solid var(--line)",
          }}
        >
          <span className="font-mono" style={{ fontSize: "0.62rem", color: "var(--muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {ctaDomain}
          </span>
          {c.cta_text && (
            <span className="font-mono" style={{ fontSize: "0.62rem", padding: "3px 8px", border: "1px solid var(--ink)", whiteSpace: "nowrap" }}>
              {sentenceCase(c.cta_text)}
            </span>
          )}
        </div>
      )}
    </div>
  );
}

/** A short, human label for an ad in the chart tooltip. Body text first —
 * titles are often just the CTA ("Install now") and don't identify the ad. */
function adLabel(c: Creative): string {
  const text = c.body_text?.trim() || c.title?.trim();
  if (text) return text;
  return c.type === "video" ? "Video ad (no text)" : c.type === "image" ? "Image ad (no text)" : "Text ad";
}

/** The ad-count curve. Hovering a week names the ads that launched in it. */
export function AdGrowth({ gem }: { gem: GemDossier }) {
  const weekMs = 7 * 24 * 3600 * 1000;
  const chart: ChartPoint[] = gem.scaling.map((s) => {
    const t0 = Date.parse(s.week_start);
    const launched = gem.creatives.filter((c) => {
      if (!c.ad_started_on) return false;
      const t = Date.parse(c.ad_started_on);
      return t >= t0 && t < t0 + weekMs;
    });
    return {
      label: fmtWeek(s.week_start),
      value: s.active_ad_count,
      tooltip: {
        title: `Week of ${monthDayLabel(s.week_start)}`,
        linesLabel: launched.length
          ? `${launched.length} ${launched.length === 1 ? "ad" : "ads"} launched this week`
          : undefined,
        lines: launched.map(adLabel),
      },
    };
  });
  const t = trendFromScaling(gem.scaling);
  // The count line is honest even when a zero start makes the multiple
  // uncomputable — only a genuinely short series is "early".
  const caption =
    gem.scaling.length < 2
      ? "Early — not many weeks of data yet."
      : `${gem.scaling[0]?.active_ad_count} → ${gem.scaling[gem.scaling.length - 1]?.active_ad_count} ads over ${gem.scaling.length} weeks${t.multiple != null ? ` · ${t.multiple}x` : ""}`;

  return (
    <section>
      <SectionTitle>Ad growth</SectionTitle>
      <p className="t-xs" style={{ color: "var(--muted)", margin: "0 0 10px" }}>
        Active ads per week. Hover a point to see the ads launched that week.
      </p>
      <SketchLineChart points={chart} height={210} highlightLast valueLabel={(v) => `${v} ads live`} />
      <p className="font-mono nums" style={{ fontSize: "var(--fs-xs)", color: "var(--muted)", marginTop: 8 }}>{caption}</p>
    </section>
  );
}

/** The collected ads, one card per ad, densest-useful grid. */
export function AdsSection({ gem }: { gem: GemDossier }) {
  const [showAll, setShowAll] = useState(false);
  const total = gem.creatives.length;
  const activeCount = gem.creatives.filter((c) => c.ad_active === true).length;
  const endedCount = gem.creatives.filter((c) => c.ad_active === false).length;
  const counts = [
    `${total} ads collected`,
    activeCount > 0 || endedCount > 0 ? `${activeCount} active now` : null,
    endedCount > 0 ? `${endedCount} ended` : null,
  ]
    .filter(Boolean)
    .join(" · ");
  const visible = showAll ? gem.creatives : gem.creatives.slice(0, INITIAL_ADS);

  if (total === 0) return null;

  return (
    <section>
      <SectionTitle>The ads</SectionTitle>
      <p className="font-mono nums" style={{ fontSize: "var(--fs-xs)", color: "var(--muted)", margin: "0 0 12px" }}>
        {counts}
      </p>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(190px, 1fr))", gap: 12 }}>
        {visible.map((c) => (
          <AdCard key={c.id} c={c} />
        ))}
      </div>
      {total > INITIAL_ADS && (
        <button
          type="button"
          onClick={() => setShowAll((s) => !s)}
          className="font-mono"
          style={{
            marginTop: 14,
            padding: "8px 14px",
            fontSize: "var(--fs-xs)",
            border: "1.5px solid var(--ink)",
            background: "transparent",
            color: "var(--ink)",
            cursor: "pointer",
          }}
        >
          {showAll ? "Show fewer" : `Show all ${total} ads`}
        </button>
      )}
    </section>
  );
}
