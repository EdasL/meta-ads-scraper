"use client";

import { useCallback, useEffect, useState } from "react";
import { useSession } from "next-auth/react";

import { getPreferenceQueue, setRating } from "@/lib/api";
import type { Rating } from "@/lib/api";
import type { PreferenceCard } from "@/types/contracts";
import { ReviewCard } from "./ReviewCard";

export function ReviewDeck() {
  const { data: session } = useSession();
  const userEmail = session?.user?.email ?? null;
  const [cards, setCards] = useState<PreferenceCard[]>([]);
  const [idx, setIdx] = useState(0);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!userEmail) return;
    let alive = true;
    setLoading(true);
    getPreferenceQueue(userEmail)
      .then((c) => {
        if (alive) setCards(c);
      })
      .catch(() => {
        if (alive) setCards([]);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [userEmail]);

  const current = cards[idx];

  const rate = useCallback(
    (rating: Rating, comment = "") => {
      if (!current || !userEmail) return;
      // Optimistic advance; persist in the background.
      setIdx((i) => i + 1);
      setRating(current.advertiser_id, userEmail, rating, comment).catch(() => {
        /* best-effort: the card has already advanced; user can re-rate from Discover */
      });
    },
    [current, userEmail],
  );

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "TEXTAREA" || t.tagName === "INPUT" || t.isContentEditable)) return;
      // The screenshot lightbox owns the arrow keys while it's open.
      if (document.querySelector("[data-review-lightbox]")) return;
      if (!current) return;
      if (e.key === "ArrowRight") rate("like");
      else if (e.key === "ArrowLeft") rate("dislike");
      else if (e.key === "ArrowDown" || e.key === " ") rate("neutral");
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [current, rate]);

  if (!userEmail) {
    return <p style={{ padding: "var(--sp-8)" }}>Sign in to review.</p>;
  }
  if (loading) {
    return <p style={{ padding: "var(--sp-8)" }}>Loading the queue…</p>;
  }
  if (!current) {
    return (
      <p style={{ padding: "var(--sp-8)" }}>
        No more apps to review. New ones appear as discovery runs.
      </p>
    );
  }

  return (
    <ReviewCard
      card={current}
      position={idx + 1}
      total={cards.length}
      onRate={rate}
    />
  );
}
