"use client";

import { useState } from "react";
import { IconHeart, IconHeartFilled } from "@tabler/icons-react";
import { SketchLineChart, type ChartPoint, type ChartTooltipItem } from "@/components/sketch";
import { trendFromScaling } from "@/lib/why";
import { adRunLine, creativeMetaLine, monthDayLabel, monthYearLabel } from "@/lib/format";
import { adLibraryAdUrl, adLibraryPageUrl, bareDomain, externalHref } from "@/lib/links";
import { saveAd, unsaveAd, useIsAdSaved } from "@/lib/useSavedAds";
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
const TOP_ADS = 5;

function SectionTitle({ children }: { children: React.ReactNode }) {
  return <h2 className="t-md w-strong" style={{ margin: "0 0 12px" }}>{children}</h2>;
}

/** Longevity badges that tie a card to the header counts: a still-running ad
 * past 30 days is a proven winner ("Ads running 30+ days"); a still-running ad
 * under 4 weeks old is one of the "New ads". Both read straight off the ad's own
 * run length, so they only appear when the status is known. */
function AdBadges({ c }: { c: Creative }) {
  const days = c.ad_days_running;
  if (c.ad_active !== true || days == null) return null;
  const badge =
    days >= 30 ? { label: "Running 30+ days", win: true } : days <= 28 ? { label: "New ad", win: false } : null;
  if (!badge) return null;
  return (
    <span
      className="font-mono"
      style={{
        fontSize: "0.62rem",
        padding: "2px 7px",
        border: "1px solid",
        borderColor: badge.win ? "var(--up)" : "var(--line)",
        background: badge.win ? "var(--up-bg)" : "transparent",
        color: badge.win ? "var(--up)" : "var(--ink-soft)",
        whiteSpace: "nowrap",
      }}
    >
      {badge.label}
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

function AdCard({ c, gem }: { c: Creative; gem: GemDossier }) {
  const [broken, setBroken] = useState(false);
  const saved = useIsAdSaved(c.external_ad_id);
  const hasMedia = !!c.snapshot_image_url && !broken;
  const alt = c.title ?? c.body_text ?? "ad creative";
  const run = adRunLine(c);
  // Reach + platforms only — US ads carry neither, so this often stays empty.
  const facts = creativeMetaLine({
    eu_total_reach: c.eu_total_reach,
    publisher_platforms: c.publisher_platforms,
  });
  const ctaDomain = c.link_url ? bareDomain(c.link_url).split("/")[0] : "";
  const onHeart = () => {
    if (!c.external_ad_id) return;
    if (saved) {
      unsaveAd(c.external_ad_id);
      return;
    }
    saveAd(c.external_ad_id, {
      advertiser_id: gem.advertiser_id,
      page_name: gem.page_name,
      app_name: gem.app_name,
      title: c.title ?? null,
      body_text: c.body_text ?? null,
      media_type: c.type,
      media_s3_key: c.s3_key ?? null,
      link_url: c.link_url ?? null,
      cta_text: c.cta_text ?? null,
      // The API binds this as `datetime=2006-01-02`, so it must stay a bare
      // YYYY-MM-DD string — anything longer is rejected and the save reverts.
      ad_started_on: c.ad_started_on ? c.ad_started_on.slice(0, 10) : null,
      ad_active: c.ad_active ?? null,
    });
  };
  return (
    <div
      style={{
        border: "1px solid var(--line)",
        background: "var(--paper)",
        display: "flex",
        flexDirection: "column",
      }}
    >
      {/* Wraps: the badge + Ad Library link already filled a narrow card, so the
          save heart has to be able to drop to its own line instead of spilling
          past the card border. */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: 8, padding: "8px 10px" }}>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6, flexWrap: "wrap", minWidth: 0 }}>
          <AdBadges c={c} />
        </span>
        {c.external_ad_id && (
          <span style={{ display: "inline-flex", alignItems: "center", gap: 6, flexShrink: 0 }}>
            <a
              href={adLibraryAdUrl(c.external_ad_id)}
              target="_blank"
              rel="noopener noreferrer"
              className="font-mono"
              style={{ fontSize: "0.62rem", color: "var(--muted)", textDecoration: "underline" }}
            >
              Ad Library ↗
            </a>
            <button
              type="button"
              onClick={onHeart}
              aria-label={saved ? "Remove from saved" : "Save ad"}
              aria-pressed={saved}
              title={saved ? "Remove from saved" : "Save ad"}
              style={{
                background: "none",
                border: "none",
                padding: 2,
                cursor: "pointer",
                display: "inline-flex",
                color: saved ? "var(--ink)" : "var(--muted)",
              }}
            >
              {saved ? <IconHeartFilled size={16} aria-hidden /> : <IconHeart size={16} stroke={1.75} aria-hidden />}
            </button>
          </span>
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
          {ctaDomain && c.link_url ? (
            <a
              href={externalHref(c.link_url)}
              target="_blank"
              rel="noopener noreferrer"
              className="font-mono"
              style={{ fontSize: "0.62rem", color: "var(--muted)", textDecoration: "underline", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
            >
              {ctaDomain}
            </a>
          ) : (
            // Empty spacer keeps the CTA pill right-aligned when the ad has no link.
            <span aria-hidden />
          )}
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

/** The ad-activity chart: ads running each week (including ended ones), with
 * thin bars for launches. Hovering a week shows mini previews of the exact
 * ads that launched in it, plus how many ended. */
export function AdGrowth({ gem }: { gem: GemDossier }) {
  const weekMs = 7 * 24 * 3600 * 1000;
  const chart: ChartPoint[] = gem.scaling.map((s) => {
    const t0 = Date.parse(s.week_start);
    const launched = gem.creatives.filter((c) => {
      if (!c.ad_started_on) return false;
      const t = Date.parse(c.ad_started_on);
      return t >= t0 && t < t0 + weekMs;
    });
    const items: ChartTooltipItem[] = launched.map((c) => ({
      text: adLabel(c),
      mediaUrl: c.snapshot_image_url,
      mediaType: c.type === "video" ? "video" : "image",
    }));
    const ended = s.ended_count ?? 0;
    return {
      label: fmtWeek(s.week_start),
      value: s.active_ad_count,
      tooltip: {
        title: `Week of ${monthDayLabel(s.week_start)}`,
        linesLabel: launched.length
          ? `${launched.length} ${launched.length === 1 ? "ad" : "ads"} launched this week`
          : undefined,
        items,
        note: ended > 0 ? `${ended} ended this week` : undefined,
      },
    };
  });
  const launchedBars = gem.scaling.map((s) => s.launched_count ?? 0);
  const hasBars = launchedBars.some((v) => v > 0);

  // Lifecycle caption: counts and dates from the full ad record. Clauses with
  // no data are omitted; older API builds (no lifecycle) fall back to the
  // window counts (trendFromScaling already withholds multiples on tiny bases).
  const lc = gem.ad_lifecycle;
  const endedTotal = lc ? lc.ads_seen - lc.active_now : 0;
  let caption: string;
  if (lc) {
    caption = [
      lc.first_ad_at ? `First ad ${monthYearLabel(lc.first_ad_at)}` : null,
      lc.ads_seen > 0 ? `${lc.ads_seen} ads seen` : null,
      `${lc.running_30d_plus} running 30+ days`,
      lc.median_run_days != null && endedTotal >= 5
        ? `median ad runs ${lc.median_run_days} ${lc.median_run_days === 1 ? "day" : "days"}`
        : null,
    ]
      .filter(Boolean)
      .join(" · ");
  } else {
    const t = trendFromScaling(gem.scaling);
    caption =
      gem.scaling.length < 2
        ? "Early — not many weeks of data yet."
        : `${gem.scaling[0]?.active_ad_count} → ${gem.scaling[gem.scaling.length - 1]?.active_ad_count} ads over ${gem.scaling.length} weeks${t.multiple != null ? ` · ${t.multiple}x` : ""}`;
  }
  const chartNote =
    lc && lc.ads_seen > 0
      ? `Ads running each week, from ${lc.ads_seen} ads in the Ad Library record, including ended ones.`
      : "Ads running each week, including ended ones.";

  return (
    <section>
      <SectionTitle>Ad activity</SectionTitle>
      <p className="t-xs" style={{ color: "var(--muted)", margin: "0 0 10px" }}>
        {chartNote}
        {hasBars && " Bars mark ads launched."}
        {" Hover a point to see the ads launched that week."}
      </p>
      <SketchLineChart
        points={chart}
        bars={hasBars ? launchedBars : undefined}
        height={210}
        highlightLast
        valueLabel={(v) => `${v} ads running`}
      />
      <p className="font-mono nums" style={{ fontSize: "var(--fs-xs)", color: "var(--muted)", marginTop: 8 }}>{caption}</p>
    </section>
  );
}

/** The collected ads, one card per ad, densest-useful grid. */
export function AdsSection({ gem }: { gem: GemDossier }) {
  // Focus on the ads that are running now. Backend returns them active-first in
  // Meta's impressions order, so the top few active ads are the best ones.
  const activeAds = gem.creatives.filter((c) => c.ad_active === true);
  const visible = activeAds.slice(0, TOP_ADS);
  const pageId = gem.company.page_id;
  // "Top N" only when we're actually trimming; otherwise it's just all the active ads.
  const title =
    activeAds.length > TOP_ADS
      ? `Top ${TOP_ADS} active ads by impressions`
      : "Active ads by impressions";

  if (activeAds.length === 0) return null;

  return (
    <section>
      <SectionTitle>{title}</SectionTitle>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(190px, 1fr))", gap: 12 }}>
        {visible.map((c) => (
          <AdCard key={c.id} c={c} gem={gem} />
        ))}
      </div>
      {activeAds.length > TOP_ADS && pageId && (
        <a
          href={adLibraryPageUrl(pageId, true)}
          target="_blank"
          rel="noreferrer"
          className="font-mono"
          style={{
            display: "inline-block",
            marginTop: 14,
            padding: "8px 14px",
            fontSize: "var(--fs-xs)",
            border: "1.5px solid var(--ink)",
            background: "transparent",
            color: "var(--ink)",
            textDecoration: "none",
            cursor: "pointer",
          }}
        >
          See all active ads ↗
        </a>
      )}
    </section>
  );
}
