"use client";

import { useState } from "react";
import Link from "next/link";
import type { HeroCreative, ShortlistRow } from "@/types/contracts";
import { growthLine } from "@/lib/why";
import { daysAgoLabel, timeAgo } from "@/lib/format";
import { AppIcon, ScoreBadge, SaveToggle, Trend } from "@/components/ui";

/**
 * One gem in the Discover feed — hero-led. The real ad creative leads (with the
 * EvidenceReel body_text fallback when there's no image), the headline is the
 * ad's title/body, the growth line quantifies the observed ad-count move, and
 * the score is demoted to a corner badge. Every value on the card comes from
 * the ads/DB row. The whole card links to the dossier; the save button opts
 * out of nav.
 */

/** Body-text tile fallback (mirrors EvidenceReel.CreativeTile) when no image. */
function FallbackTile({ hero }: { hero: HeroCreative | null }) {
  const glyph = hero?.type === "video" ? "▶" : "▣";
  const text = hero?.body_text ?? hero?.title;
  return (
    <div
      style={{
        width: "100%",
        height: "100%",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        textAlign: "center",
        padding: 14,
        background: "var(--surface-3)",
      }}
    >
      <div className="t-2xl w-heavy" aria-hidden style={{ color: "var(--faint)" }}>{glyph}</div>
      {text && (
        <div className="font-mono" style={{ fontSize: "var(--fs-xs)", color: "var(--ink-soft)", marginTop: 8, display: "-webkit-box", WebkitLineClamp: 3, WebkitBoxOrient: "vertical", overflow: "hidden" }}>
          {text}
        </div>
      )}
    </div>
  );
}

function CardHero({ hero, score }: { hero: HeroCreative | null; score: number }) {
  const [broken, setBroken] = useState(false);
  const hasMedia = !!hero?.snapshot_image_url && !broken;
  const isVideo = hero?.type === "video";
  return (
    <div
      style={{
        position: "relative",
        width: "100%",
        aspectRatio: "16 / 10",
        border: "1.5px solid var(--ink)",
        background: "var(--bg)",
        overflow: "hidden",
      }}
    >
      {hasMedia ? (
        isVideo ? (
          <video
            src={hero!.snapshot_image_url}
            muted
            playsInline
            preload="metadata"
            onError={() => setBroken(true)}
            style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }}
          />
        ) : (
          // eslint-disable-next-line @next/next/no-img-element -- dynamic /api/media proxy thumbnails; next/image is a poor fit for an internal tool
          <img
            src={hero!.snapshot_image_url}
            alt={hero!.title ?? hero!.body_text ?? "ad creative"}
            loading="lazy"
            onError={() => setBroken(true)}
            style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }}
          />
        )
      ) : (
        <FallbackTile hero={hero} />
      )}
      <div style={{ position: "absolute", top: 6, right: 6 }}>
        <ScoreBadge score={score} variant="corner" />
      </div>
    </div>
  );
}

export function GemCard({ row }: { row: ShortlistRow }) {
  // Captured once at mount (lazy init keeps Date.now() out of render purity).
  const [nowMs] = useState(() => Date.now());
  const g = growthLine(row.growth, row);
  const headline = row.hero_creative?.title ?? row.hero_creative?.body_text ?? (row.app_name ?? row.page_name);
  const footerName = row.app_name ?? row.page_name;

  return (
    <Link
      href={`/gems/${row.advertiser_id}`}
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 12,
        textDecoration: "none",
        color: "var(--ink)",
        background: "var(--paper)",
        border: "1.5px solid var(--ink)",
        boxShadow: "var(--shadow-hard)",
        padding: 16,
      }}
    >
      <CardHero hero={row.hero_creative} score={row.gem_score} />

      <div
        className="t-md w-strong"
        style={{
          margin: 0,
          display: "-webkit-box",
          WebkitLineClamp: 2,
          WebkitBoxOrient: "vertical",
          overflow: "hidden",
          wordBreak: "break-word",
        }}
      >
        {headline}
      </div>

      <div>
        <div className="eyebrow" style={{ marginBottom: 4 }}>{g.kind === "growth" ? "ad growth" : "ads live"}</div>
        <Trend glyph={g.glyph} label={g.text} tone={g.tone} />
      </div>

      <div
        className="font-mono nums"
        style={{ display: "flex", flexWrap: "wrap", gap: "2px 10px", fontSize: "var(--fs-xs)", color: "var(--muted)" }}
      >
        <span>oldest ad {daysAgoLabel(row.max_longevity_days)}</span>
        <span aria-hidden>·</span>
        <span>updated {timeAgo(row.refreshed_at, nowMs)}</span>
      </div>

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, marginTop: "auto", paddingTop: 10, borderTop: "1px solid var(--line)" }}>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 8, minWidth: 0 }}>
          <AppIcon src={row.icon_url} name={footerName} />
          <span className="font-mono" style={{ fontSize: "var(--fs-xs)", color: "var(--muted)", minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {footerName} · {row.cohort}
            {row.app_id === null && " · web"}
          </span>
        </span>
        <SaveToggle row={row} size="sm" />
      </div>
    </Link>
  );
}
