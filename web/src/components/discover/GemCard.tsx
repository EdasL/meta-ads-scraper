"use client";

import { useState } from "react";
import Link from "next/link";
import type { HeroCreative, ShortlistRow } from "@/types/contracts";
import { growthLine } from "@/lib/why";
import { AppIcon, ScoreBadge, SaveToggle, Trend } from "@/components/ui";

/**
 * One gem in the Discover feed — everything on the card is observed data: the
 * real ad creative leads, then app logo + name, then the ad-growth line from
 * the weekly series. The whole card links to the dossier; the save button
 * opts out of nav.
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
  const g = growthLine(row.growth, row);
  const name = row.app_name ?? row.page_name;

  return (
    <Link
      href={`/gems/${row.advertiser_id}`}
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 8,
        textDecoration: "none",
        color: "var(--ink)",
        background: "var(--paper)",
        border: "1.5px solid var(--ink)",
        boxShadow: "var(--shadow-hard)",
        padding: 10,
      }}
    >
      <CardHero hero={row.hero_creative} score={row.gem_score} />

      <div style={{ display: "flex", alignItems: "flex-start", gap: 10 }}>
        <AppIcon src={row.icon_url} name={name} size={44} />
        <div
          className="t-sm w-strong"
          style={{
            flex: 1,
            minWidth: 0,
            display: "-webkit-box",
            WebkitLineClamp: 2,
            WebkitBoxOrient: "vertical",
            overflow: "hidden",
            wordBreak: "break-word",
            // Always reserve two lines so the growth row sits at the same
            // height on every card.
            minHeight: "calc(2 * var(--lh-sm))",
          }}
        >
          {name}
        </div>
        <SaveToggle row={row} size="sm" />
      </div>

      <div>
        <div className="eyebrow" style={{ marginBottom: 2 }}>{g.kind === "growth" ? "Ad growth" : "Ads live"}</div>
        <Trend glyph={g.glyph} label={g.text} tone={g.tone} />
        {(row.longrun_ad_count ?? 0) > 0 && (
          <div className="font-mono nums" style={{ fontSize: "var(--fs-xs)", color: "var(--muted)", marginTop: 2 }}>
            {row.longrun_ad_count} running 30+ days
          </div>
        )}
      </div>
    </Link>
  );
}
