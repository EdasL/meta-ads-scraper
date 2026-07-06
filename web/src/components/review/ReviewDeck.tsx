"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useSession } from "next-auth/react";

import { getPreferenceQueue, setRating } from "@/lib/api";
import type { Rating } from "@/lib/api";
import { REFILL_LIMIT, RefillScheduler, mergeRefill, refillExclude } from "@/lib/reviewBuffer";
import type { PreferenceCard } from "@/types/contracts";
import { ReviewCard } from "./ReviewCard";

export function ReviewDeck() {
  const { data: session } = useSession();
  const userEmail = session?.user?.email ?? null;
  const [cards, setCards] = useState<PreferenceCard[]>([]);
  const [idx, setIdx] = useState(0);
  const [loading, setLoading] = useState(true);
  // True once a refill comes back empty: every candidate is rated.
  const [exhausted, setExhausted] = useState(false);
  const ratedIdsRef = useRef<number[]>([]);
  const schedulerRef = useRef<RefillScheduler | null>(null);
  // Refs mirror state for the async refill closure (it must read the values
  // at run time, not the values captured when the scheduler was built). The
  // mirror runs in an effect — writing a ref during render is disallowed.
  const stateRef = useRef({ cards, idx });
  useEffect(() => {
    stateRef.current = { cards, idx };
  });

  // Reset the deck when the signed-in user changes. Adjusting state during
  // render (guarded by the change) is React's recommended replacement for
  // calling setState synchronously in an effect — it avoids the cascading-
  // render warning and applies before the children see stale idx/loading.
  const [prevEmail, setPrevEmail] = useState(userEmail);
  if (userEmail !== prevEmail) {
    setPrevEmail(userEmail);
    setIdx(0);
    setLoading(true);
    setExhausted(false);
  }

  useEffect(() => {
    if (!userEmail) return;
    let alive = true;
    // Fresh user (or mount): drop any session-rated ids from the prior deck.
    ratedIdsRef.current = [];
    // Small initial buffer; background refills keep it topped up and freshly
    // ranked as the user rates (each refill rescored from the latest ratings).
    getPreferenceQueue(userEmail, REFILL_LIMIT)
      .then((c) => {
        if (!alive) return;
        setCards(c);
        setExhausted(c.length === 0);
      })
      .catch(() => {
        if (alive) setCards([]);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });

    const scheduler = new RefillScheduler(async () => {
      const { cards: cur, idx: curIdx } = stateRef.current;
      const exclude = refillExclude(cur, curIdx, ratedIdsRef.current);
      const fresh = await getPreferenceQueue(userEmail, REFILL_LIMIT, exclude);
      setExhausted(fresh.length === 0);
      if (fresh.length > 0) {
        setCards((prev) => mergeRefill(prev, stateRef.current.idx, fresh));
      }
    });
    schedulerRef.current = scheduler;
    return () => {
      alive = false;
      scheduler.dispose();
      schedulerRef.current = null;
    };
  }, [userEmail]);

  const current = cards[idx];

  // If the user rates faster than refills land, the buffer can drain while
  // more candidates exist — nudge another refill instead of dead-ending.
  useEffect(() => {
    if (!loading && !current && !exhausted) schedulerRef.current?.request();
  }, [loading, current, exhausted]);

  const rate = useCallback(
    (rating: Rating, comment = "") => {
      if (!current || !userEmail) return;
      ratedIdsRef.current.push(current.advertiser_id);
      // Optimistic advance; persist in the background, then refill so the next
      // cards reflect the rating just given.
      setIdx((i) => i + 1);
      setRating(current.advertiser_id, userEmail, rating, comment)
        .catch(() => {
          /* best-effort: the card has already advanced; user can re-rate from Discover */
        })
        .finally(() => schedulerRef.current?.request());
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
    if (!exhausted) {
      return <p style={{ padding: "var(--sp-8)" }}>Loading more…</p>;
    }
    return (
      <p style={{ padding: "var(--sp-8)" }}>
        All caught up — you’ve reviewed every app in the queue. New ones appear as
        discovery runs.
      </p>
    );
  }

  return <ReviewCard card={current} onRate={rate} />;
}
