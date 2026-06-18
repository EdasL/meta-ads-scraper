"use client";

import { useEffect, useState } from "react";
import type { PreferenceCard, GemDossier } from "@/types/contracts";
import { getGem } from "@/lib/api";
import type { Rating } from "@/lib/api";
import { MIN_PCT_BASE } from "@/lib/why";
import { AppIcon } from "@/components/ui";
import { AdsSection } from "@/components/detail/EvidenceReel";

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

  const growthLine =
    card.growth?.pct_change != null && card.growth.first_count >= MIN_PCT_BASE
      ? `${card.growth.pct_change >= 0 ? "+" : ""}${Math.round(card.growth.pct_change)}% ${card.growth.weeks}w`
      : null;

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

      {/* Signal row */}
      <div
        className="t-sm"
        style={{ display: "flex", gap: "var(--sp-6)", marginTop: "var(--sp-3)", flexWrap: "wrap" }}
      >
        <span className="nums">{card.active_ad_count} active ads</span>
        <span className="nums">GemScore {Math.round(card.gem_score)}</span>
        {growthLine ? <span className="nums">{growthLine}</span> : null}
      </div>

      {card.why ? (
        <div className="t-sm" style={{ marginTop: "var(--sp-2)", color: "var(--ink-soft)" }}>
          {card.why}
        </div>
      ) : null}

      {/* Quiz / funnel — prominent, opens in a new tab so it can be checked */}
      {funnelUrl ? (
        <a
          href={funnelUrl}
          target="_blank"
          rel="noreferrer"
          className="t-sm w-strong"
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            marginTop: "var(--sp-4)",
            padding: "6px 14px",
            border: "1.5px solid var(--ink)",
            color: "var(--ink)",
            textDecoration: "none",
          }}
        >
          Open quiz funnel ↗
        </a>
      ) : null}

      {/* The ads — reuses the gem page's active-ads section */}
      <div style={{ marginTop: "var(--sp-5)" }}>
        {dossier ? (
          <AdsSection gem={dossier} />
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
