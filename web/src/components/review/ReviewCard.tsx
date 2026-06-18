"use client";

import { useState } from "react";
import type { PreferenceCard } from "@/types/contracts";
import type { Rating } from "@/lib/api";
import { MIN_PCT_BASE } from "@/lib/why";

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

  // Hero image: ad creative snapshot, fall back to app icon.
  const heroUrl = card.hero_creative?.snapshot_image_url ?? card.icon_url;

  function handleRate(rating: Rating) {
    setPending(rating);
    onRate(rating, note);
  }

  const growthLine =
    card.growth?.pct_change != null && card.growth.first_count >= MIN_PCT_BASE
      ? `${card.growth.pct_change >= 0 ? "+" : ""}${Math.round(card.growth.pct_change)}% ${card.growth.weeks}w`
      : null;

  return (
    <div style={{ maxWidth: 520, margin: "0 auto", padding: "var(--sp-6)" }}>
      <div className="t-sm" style={{ color: "var(--ink-soft)", marginBottom: "var(--sp-4)" }}>
        {position} / {total}
      </div>

      {heroUrl ? (
        <img
          src={heroUrl}
          alt={card.app_name ?? card.page_name}
          style={{
            width: "100%",
            aspectRatio: "1 / 1",
            objectFit: "cover",
            border: "1.5px solid var(--ink)",
            boxShadow: "var(--shadow-hard)",
            display: "block",
          }}
        />
      ) : null}

      <h1 className="t-2xl w-strong" style={{ marginTop: "var(--sp-4)", marginBottom: 0 }}>
        {card.app_name ?? card.page_name}
      </h1>
      <div className="t-sm" style={{ color: "var(--ink-soft)", marginTop: "var(--sp-1)" }}>
        {card.cohort}
      </div>
      {card.tagline ? (
        <p className="t-base" style={{ marginTop: "var(--sp-2)" }}>
          {card.tagline}
        </p>
      ) : null}

      <div
        className="t-sm"
        style={{ display: "flex", gap: "var(--sp-6)", marginTop: "var(--sp-4)", flexWrap: "wrap" }}
      >
        <span className="nums">{card.active_ad_count} active ads</span>
        <span className="nums">GemScore {Math.round(card.gem_score)}</span>
        {growthLine ? <span className="nums">{growthLine}</span> : null}
        {card.funnel_url ? (
          <a href={card.funnel_url} target="_blank" rel="noreferrer" className="t-sm">
            Funnel
          </a>
        ) : null}
      </div>

      {card.why ? (
        <div className="t-sm" style={{ marginTop: "var(--sp-2)", color: "var(--ink-soft)" }}>
          {card.why}
        </div>
      ) : null}

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
