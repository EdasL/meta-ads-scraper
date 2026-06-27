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

// Google Play screenshot URLs load more reliably (and smaller) when a size is
// requested — raw URLs get throttled when several load at once, leaving broken
// thumbnails. Apple/other CDNs are passed through untouched.
function sizedShot(url: string, h = 300): string {
  if (/googleusercontent\.com/.test(url) && !/=[wh]\d/.test(url)) return `${url}=h${h}`;
  return url;
}

// One store screenshot thumbnail; hides itself if the image fails to load.
// Clicking opens the full-size lightbox.
function Screenshot({ src, index, onOpen }: { src: string; index: number; onOpen: () => void }) {
  const [broken, setBroken] = useState(false);
  if (broken) return null;
  return (
    <img
      src={sizedShot(src)}
      alt={`Screenshot ${index + 1}`}
      loading="lazy"
      onError={() => setBroken(true)}
      onClick={onOpen}
      style={{
        height: 150,
        width: "auto",
        border: "1px solid var(--line)",
        borderRadius: 6,
        flexShrink: 0,
        display: "block",
        cursor: "zoom-in",
      }}
    />
  );
}

// Full-screen screenshot viewer. Owns its own ←/→/Esc keys; the deck's rating
// keys stand down while it's open (it marks itself with data-review-lightbox).
function ScreenshotLightbox({
  shots,
  index,
  onIndex,
  onClose,
}: {
  shots: string[];
  index: number;
  onIndex: (i: number) => void;
  onClose: () => void;
}) {
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
      else if (e.key === "ArrowLeft") {
        e.preventDefault();
        onIndex((index - 1 + shots.length) % shots.length);
      } else if (e.key === "ArrowRight") {
        e.preventDefault();
        onIndex((index + 1) % shots.length);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [index, shots.length, onIndex, onClose]);

  const navBtn: React.CSSProperties = {
    position: "absolute",
    top: "50%",
    transform: "translateY(-50%)",
    fontSize: 40,
    lineHeight: 1,
    color: "#fff",
    background: "transparent",
    border: "none",
    cursor: "pointer",
    padding: "0 var(--sp-4)",
    userSelect: "none",
  };

  return (
    <div
      data-review-lightbox
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 50,
        background: "rgba(0,0,0,0.85)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      <button
        type="button"
        aria-label="Previous screenshot"
        style={{ ...navBtn, left: 0 }}
        onClick={(e) => {
          e.stopPropagation();
          onIndex((index - 1 + shots.length) % shots.length);
        }}
      >
        ‹
      </button>
      <img
        src={sizedShot(shots[index], 1000)}
        alt={`Screenshot ${index + 1}`}
        onClick={(e) => e.stopPropagation()}
        style={{ maxHeight: "90vh", maxWidth: "80vw", objectFit: "contain" }}
      />
      <button
        type="button"
        aria-label="Next screenshot"
        style={{ ...navBtn, right: 0 }}
        onClick={(e) => {
          e.stopPropagation();
          onIndex((index + 1) % shots.length);
        }}
      >
        ›
      </button>
      <div
        className="t-sm"
        style={{ position: "absolute", bottom: 16, color: "#fff", userSelect: "none" }}
      >
        {index + 1} / {shots.length} · ← → to move · Esc to close
      </div>
    </div>
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
  onRate,
}: {
  card: PreferenceCard;
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
  const [lightbox, setLightbox] = useState<number | null>(null);

  // Reset the per-card dossier state when the card changes. Adjusting state
  // during render (guarded by the change) is React's recommended replacement
  // for calling setState synchronously in an effect — no cascading-render
  // warning, and the reset applies before the stale dossier paints.
  const [prevAdvId, setPrevAdvId] = useState(card.advertiser_id);
  if (card.advertiser_id !== prevAdvId) {
    setPrevAdvId(card.advertiser_id);
    setDossier(null);
    setLoadingDossier(true);
    setLightbox(null);
  }

  useEffect(() => {
    let alive = true;
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

  // App store screenshots from the richest linked listing (Apple often has
  // none while Play has the full set).
  const screenshots = (
    (dossier?.apps ?? []).find((a) => a.screenshots && a.screenshots.length)?.screenshots ?? []
  ).slice(0, 6);

  return (
    <div style={{ maxWidth: 660, margin: "0 auto", padding: "var(--sp-6)" }}>
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

      {/* App store screenshots — small strip from the linked listing */}
      {screenshots.length ? (
        <div style={{ marginTop: "var(--sp-5)" }}>
          <div className="t-sm w-strong" style={{ marginBottom: "var(--sp-2)" }}>
            Screenshots
          </div>
          <div style={{ display: "flex", gap: "var(--sp-2)", overflowX: "auto", paddingBottom: 4 }}>
            {screenshots.map((src, i) => (
              <Screenshot key={i} src={src} index={i} onOpen={() => setLightbox(i)} />
            ))}
          </div>
        </div>
      ) : null}

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

      {lightbox != null && screenshots[lightbox] ? (
        <ScreenshotLightbox
          shots={screenshots}
          index={lightbox}
          onIndex={setLightbox}
          onClose={() => setLightbox(null)}
        />
      ) : null}
    </div>
  );
}
