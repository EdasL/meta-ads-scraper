"use client";

import { useEffect, useState } from "react";
import type { PreferenceCard, GemDossier, Creative } from "@/types/contracts";
import { getGem } from "@/lib/api";
import type { Rating } from "@/lib/api";
import { growthLine, monthLabel } from "@/lib/why";
import { compactCount } from "@/lib/format";
import { AppIcon, ScoreBadge, Trend } from "@/components/ui";
import { AdGrowth } from "@/components/detail/EvidenceReel";

const TOP_MEDIA = 6;

// Funnel destination as a bare hostname (e.g. "appstack.link") so it's honest
// about where the ad's link actually goes — most "funnels" are store/MMP
// bounces, not quiz pages, and the domain makes that visible.
function funnelHost(url?: string): string | null {
  if (!url) return null;
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

// "+1.2K in 28d" from a raw delta; a real 0 still renders (flat is information).
function deltaLine(delta: number | undefined): string | null {
  if (delta == null) return null;
  return `${delta > 0 ? "+" : ""}${compactCount(delta)} in 28d`;
}

const adLibraryAdUrl = (id?: string) =>
  id ? `https://www.facebook.com/ads/library/?id=${id}` : undefined;

// One ad creative rendered as a small image/video thumbnail (no text card).
function AdThumb({ c }: { c: Creative }) {
  const href = adLibraryAdUrl(c.external_ad_id);
  const media =
    c.type === "video" ? (
      <video
        src={c.snapshot_image_url}
        muted
        loop
        playsInline
        style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }}
      />
    ) : (
      <img
        src={c.snapshot_image_url}
        alt={c.title ?? "ad creative"}
        loading="lazy"
        style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }}
      />
    );
  const frame: React.CSSProperties = {
    width: 104,
    height: 104,
    border: "1.5px solid var(--ink)",
    overflow: "hidden",
    flexShrink: 0,
    background: "var(--surface-3)",
  };
  return href ? (
    <a href={href} target="_blank" rel="noreferrer" title="Open in Ad Library" style={frame}>
      {media}
    </a>
  ) : (
    <div style={frame}>{media}</div>
  );
}

// One label/value row in the facts grid (mirrors a Discover-table column).
function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <>
      <dt className="t-sm" style={{ color: "var(--ink-soft)" }}>
        {label}
      </dt>
      <dd className="t-sm" style={{ margin: 0 }}>
        {children}
      </dd>
    </>
  );
}

const btnBase: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  cursor: "pointer",
  padding: "6px 14px",
  border: "1.5px solid var(--ink)",
  background: "transparent",
  color: "var(--ink)",
  fontSize: 14,
  lineHeight: 1,
  userSelect: "none",
};

const btnActive: React.CSSProperties = {
  background: "var(--ink)",
  color: "var(--bg)",
};

export function ReviewCard({
  card,
  position,
  total,
  onRate,
}: {
  card: PreferenceCard;
  position: number;
  total: number;
  onRate: (rating: Rating, comment?: string) => void;
}) {
  const [note, setNote] = useState("");
  const [showNote, setShowNote] = useState(false);
  const [pending, setPending] = useState<Rating | null>(null);

  // The queue payload is lightweight (one hero creative). Fetch the full
  // dossier for the current app so the card mirrors the gem page's main info:
  // the real ad set + store data. Loads per card; the core info renders
  // immediately from `card` while the ads stream in.
  const [dossier, setDossier] = useState<GemDossier | null>(null);
  const [loadingDossier, setLoadingDossier] = useState(true);

  useEffect(() => {
    let alive = true;
    setDossier(null);
    setLoadingDossier(true);
    getGem(card.advertiser_id)
      .then((d) => alive && setDossier(d))
      .catch(() => alive && setDossier(null))
      .finally(() => alive && setLoadingDossier(false));
    return () => {
      alive = false;
    };
  }, [card.advertiser_id]);

  function handleRate(rating: Rating) {
    setPending(rating);
    onRate(rating, note);
  }

  const name = card.app_name ?? card.page_name ?? "Untitled";
  const iconUrl = dossier?.icon_url ?? card.icon_url;
  const funnelUrl = dossier?.funnel_url ?? card.funnel_url;

  // Same facts the Discover table shows per row.
  const trend = growthLine(card.growth, {
    active_ad_count: card.active_ad_count,
    first_ad_at: card.first_ad_at,
  });
  const winners = card.longrun_ad_count ?? 0;
  // Traction: Play installs for Android, store reviews for iOS (Apple never
  // exposes installs) — same adaptive signal as the table.
  const useInstalls =
    card.store === "android" || (card.store == null && card.installs != null);
  const tractionValue = useInstalls ? card.installs : card.rating_count;
  const tractionDelta = useInstalls ? card.installs_delta_28d : card.rating_count_delta_28d;
  const tractionSignal = useInstalls ? "installs" : "reviews";
  const funnelHostName = funnelHost(funnelUrl);
  const firstAd = monthLabel(card.first_ad_at) ?? "—";

  // Ads as media only — drop text-only creatives; active first; cap the grid.
  const mediaAds = (dossier?.creatives ?? [])
    .filter((c) => !!c.snapshot_image_url && (c.type === "image" || c.type === "video"))
    .sort((a, b) => Number(b.ad_active) - Number(a.ad_active))
    .slice(0, TOP_MEDIA);

  return (
    <div style={{ maxWidth: 660, margin: "0 auto", padding: "var(--sp-6)" }}>
      <div className="t-sm" style={{ color: "var(--ink-soft)", marginBottom: "var(--sp-4)" }}>
        {position} / {total}
      </div>

      {/* Identity row — small logo + name + cohort */}
      <div style={{ display: "flex", gap: "var(--sp-3)", alignItems: "center" }}>
        <AppIcon src={iconUrl} name={name} size={44} />
        <div style={{ minWidth: 0 }}>
          <h1 className="t-xl w-strong" style={{ margin: 0, lineHeight: 1.15 }}>
            {name}
          </h1>
          <div className="t-sm" style={{ color: "var(--ink-soft)", marginTop: 2 }}>
            {card.cohort}
          </div>
        </div>
      </div>

      {card.tagline ? (
        <p className="t-base" style={{ marginTop: "var(--sp-3)", marginBottom: 0 }}>
          {card.tagline}
        </p>
      ) : null}

      {/* The data behind the score — same columns as the Discover table */}
      <dl
        style={{
          display: "grid",
          gridTemplateColumns: "auto 1fr",
          gap: "var(--sp-2) var(--sp-4)",
          alignItems: "baseline",
          margin: "var(--sp-4) 0 0",
        }}
      >
        <Fact label="GemScore">
          <ScoreBadge score={card.gem_score} variant="card" />
        </Fact>
        <Fact label="Ads">
          <span style={{ display: "inline-flex", gap: "var(--sp-2)", alignItems: "baseline", flexWrap: "wrap" }}>
            <span className="nums">{card.active_ad_count} active</span>
            <Trend
              glyph={trend.glyph}
              label={winners > 0 ? `${winners} run 30d+` : trend.text}
              tone={trend.tone}
            />
          </span>
        </Fact>
        <Fact label="Traction">
          {tractionValue != null ? (
            <span className="nums">
              {compactCount(tractionValue)} {tractionSignal}
              {deltaLine(tractionDelta) ? ` · ${deltaLine(tractionDelta)}` : ""}
            </span>
          ) : (
            "—"
          )}
        </Fact>
        <Fact label="First ad">
          <span className="nums">{firstAd}</span>
        </Fact>
        <Fact label="Funnel">
          {funnelHostName ? (
            <a
              href={funnelUrl}
              target="_blank"
              rel="noreferrer"
              title={funnelUrl}
              style={{ color: "var(--ink)", textDecoration: "underline" }}
            >
              {funnelHostName} ↗
            </a>
          ) : (
            <span style={{ color: "var(--ink-soft)" }}>none — ads go to the store</span>
          )}
        </Fact>
      </dl>

      {/* Ads-over-time diagram (reused from the gem page) */}
      {dossier ? (
        <div style={{ marginTop: "var(--sp-5)" }}>
          <AdGrowth gem={dossier} />
        </div>
      ) : null}

      {/* The ads — image/video creatives only, as small thumbnails */}
      <div style={{ marginTop: "var(--sp-4)" }}>
        {dossier ? (
          mediaAds.length ? (
            <>
              <div className="t-sm w-strong" style={{ marginBottom: "var(--sp-2)" }}>
                Ads
              </div>
              <div style={{ display: "flex", gap: "var(--sp-2)", flexWrap: "wrap" }}>
                {mediaAds.map((c) => (
                  <AdThumb key={c.id} c={c} />
                ))}
              </div>
            </>
          ) : (
            <div className="t-sm" style={{ color: "var(--ink-soft)" }}>
              No ad images or videos captured.
            </div>
          )
        ) : loadingDossier ? (
          <div className="t-sm" style={{ color: "var(--ink-soft)" }}>
            Loading ads…
          </div>
        ) : null}
      </div>

      {/* Rating controls */}
      <div style={{ display: "flex", gap: "var(--sp-3)", marginTop: "var(--sp-6)" }}>
        <button
          type="button"
          aria-label="Dislike"
          title="Dislike"
          style={pending === "dislike" ? { ...btnBase, ...btnActive } : btnBase}
          onClick={() => handleRate("dislike")}
        >
          👎 Dislike
        </button>
        <button
          type="button"
          aria-label="Neutral"
          title="Neutral"
          style={pending === "neutral" ? { ...btnBase, ...btnActive } : btnBase}
          onClick={() => handleRate("neutral")}
        >
          ➖ Neutral
        </button>
        <button
          type="button"
          aria-label="Like"
          title="Like"
          style={pending === "like" ? { ...btnBase, ...btnActive } : btnBase}
          onClick={() => handleRate("like")}
        >
          👍 Like
        </button>
        <button
          type="button"
          aria-label={showNote ? "Close note" : "Add note"}
          title={showNote ? "Close note" : "Add note"}
          style={note.trim().length > 0 ? { ...btnBase, ...btnActive } : btnBase}
          onClick={() => setShowNote((s) => !s)}
        >
          ✏️
        </button>
      </div>

      {showNote ? (
        <textarea
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="Optional note (saved with your rating)"
          style={{
            width: "100%",
            marginTop: "var(--sp-4)",
            border: "1.5px solid var(--ink)",
            padding: "var(--sp-2)",
            background: "transparent",
            color: "var(--ink)",
            fontSize: 13,
            resize: "vertical",
            minHeight: 60,
          }}
        />
      ) : null}

      <div className="t-xs" style={{ marginTop: "var(--sp-4)", color: "var(--ink-soft)" }}>
        ← dislike · ↓ neutral · → like
      </div>
    </div>
  );
}
