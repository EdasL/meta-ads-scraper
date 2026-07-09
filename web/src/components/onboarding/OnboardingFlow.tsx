"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useSession } from "next-auth/react";

import { completeOnboarding, getPreferenceQueue, setRating } from "@/lib/api";
import type { Rating } from "@/lib/api";
import type { PreferenceCard } from "@/types/contracts";
import { ReviewCard } from "@/components/review/ReviewCard";

const DECK_SIZE = 15;

const PANELS = [
  {
    title: "Find apps before they're obvious",
    body: "Field Desk watches Meta's ad library for mobile apps that are quietly scaling paid campaigns — small pages with real, growing ad spend. You see them while they're still emerging, not after they top the charts.",
  },
  {
    title: "GemScore ranks the signal",
    body: "Every app gets a GemScore: how long its winning ads have been running, how fast its campaign is growing, and how much reach it's buying — measured against its cohort. Higher means a stronger scaling signal.",
  },
  {
    title: "Teach it your taste",
    body: "Rate apps in Review — like, skip, or pass. Once you've liked about 8, your Discover feed reranks around what you like. First, a short calibration round.",
  },
] as const;

export function OnboardingFlow() {
  const router = useRouter();
  const { data: session } = useSession();
  const userEmail = session?.user?.email ?? null;

  const [panel, setPanel] = useState(0); // 0..2 explainer, 3 = deck
  const [cards, setCards] = useState<PreferenceCard[]>([]);
  const [idx, setIdx] = useState(0);
  const [loading, setLoading] = useState(true);
  const [deckError, setDeckError] = useState(false);
  const [finishing, setFinishing] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);

  const inDeck = panel === PANELS.length;

  // Each (re)entry into the deck, each retry, and each signed-in-user change
  // bumps the fetch token — the same three triggers as the fetch effect's deps
  // below, so loading/error reset in lockstep with every refetch (a user switch
  // must not leave a stale error banner over the new user's fetch — mirrors
  // ReviewDeck's user-change reset). Resetting during render (guarded by the
  // token change) is React's recommended replacement for calling setState
  // synchronously in an effect — it avoids the cascading-render warning and
  // applies before the fetch runs.
  const fetchToken = inDeck ? `${refreshKey}:${userEmail ?? ""}` : null;
  const [prevToken, setPrevToken] = useState(fetchToken);
  if (fetchToken !== prevToken) {
    setPrevToken(fetchToken);
    if (inDeck) {
      setLoading(true);
      setDeckError(false);
    }
  }

  useEffect(() => {
    if (!inDeck || !userEmail) return;
    let alive = true;
    getPreferenceQueue(userEmail, DECK_SIZE)
      .then((c) => {
        if (alive) setCards(c);
      })
      .catch(() => {
        if (alive) setDeckError(true);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [inDeck, userEmail, refreshKey]);

  async function finish() {
    if (finishing) return;
    setFinishing(true);
    try {
      await completeOnboarding();
    } catch {
      // Best-effort: the gate re-shows onboarding next visit if this failed.
    }
    router.replace("/discover");
  }

  function rate(rating: Rating, comment = "") {
    const current = cards[idx];
    if (!current || !userEmail) return;
    setIdx((i) => i + 1);
    setRating(current.advertiser_id, userEmail, rating, comment).catch(() => {
      /* best-effort, same policy as the review deck */
    });
    if (idx + 1 >= cards.length) void finish();
  }

  const shellStyle: React.CSSProperties = {
    maxWidth: 720,
    margin: "0 auto",
    padding: "48px 22px 64px",
    position: "relative",
    zIndex: 1,
  };

  if (!inDeck) {
    const p = PANELS[panel];
    return (
      <main style={shellStyle}>
        <p className="t-sm w-ui nums" style={{ color: "var(--muted)", margin: 0 }}>
          {panel + 1} of {PANELS.length}
        </p>
        <h1 className="display-x" style={{ fontSize: "2.2rem", marginTop: 10 }}>{p.title}</h1>
        <p style={{ marginTop: 16, lineHeight: 1.6, maxWidth: "56ch" }}>{p.body}</p>
        <div style={{ display: "flex", gap: 12, marginTop: 32 }}>
          {panel > 0 && (
            <button className="t-sm w-ui" onClick={() => setPanel((v) => v - 1)}
              style={{ background: "transparent", border: "1.5px solid var(--ink)", padding: "12px 22px", cursor: "pointer", color: "var(--ink)" }}>
              Back
            </button>
          )}
          <button className="t-sm w-strong" onClick={() => setPanel((v) => v + 1)}
            style={{ background: "var(--ink)", color: "var(--paper)", border: "none", padding: "12px 22px", cursor: "pointer" }}>
            {panel === PANELS.length - 1 ? "Start calibrating" : "Next"}
          </button>
        </div>
      </main>
    );
  }

  const current = cards[idx];

  return (
    <main style={shellStyle}>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between" }}>
        <div>
          <p className="t-sm w-strong nums" style={{ margin: 0 }}>
            Calibrate your feed{cards.length > 0 ? ` — ${Math.min(idx + 1, cards.length)} of ${cards.length}` : ""}
          </p>
          <p className="t-sm w-ui" style={{ color: "var(--muted)", marginTop: 4 }}>
            Like 8 or more apps and Discover starts ranking around your taste.
          </p>
        </div>
        <button className="t-sm w-ui" onClick={() => void finish()} disabled={finishing}
          style={{ background: "transparent", border: "none", cursor: "pointer", color: "var(--muted)", textDecoration: "underline" }}>
          Skip for now
        </button>
      </div>
      <div style={{ marginTop: 24 }}>
        {loading && <p>Loading apps to rate…</p>}
        {deckError && !loading && (
          <p>
            Couldn&apos;t load the deck.{" "}
            <button className="t-sm w-ui" onClick={() => setRefreshKey((k) => k + 1)}
              style={{ background: "transparent", border: "none", cursor: "pointer", textDecoration: "underline", color: "var(--ink)" }}>
              Retry
            </button>{" "}
            or skip — you can rate any time in Review.
          </p>
        )}
        {!loading && !deckError && !current && !finishing && cards.length === 0 && (
          <p>Nothing to rate right now — skip ahead; new apps appear as discovery runs.</p>
        )}
        {!loading && current && <ReviewCard card={current} onRate={rate} />}
      </div>
    </main>
  );
}
